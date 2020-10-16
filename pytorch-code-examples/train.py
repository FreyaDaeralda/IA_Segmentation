"""Train the model"""

import argparse
import logging
import os

import apex
from apex import amp
from apex.parallel import DistributedDataParallel as DDP
import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.optim as optim
from tqdm import tqdm

import utils
from utils import reduce_tensor
import model.net as net
import model.data_loader as data_loader
from evaluate import evaluate

parser = argparse.ArgumentParser()
parser.add_argument('--data_dir', default='data/64x64_SIGNS',
                    help="Directory containing the dataset")
parser.add_argument('--model_dir', default='experiments/base_model',
                    help="Directory containing params.json")
parser.add_argument('--restore_file', default=None,
                    help="Optional, name of the file in --model_dir containing weights to reload before \
                    training")  # 'best' or 'train'
parser.add_argument('--local_rank', default=-1, type=int)


def train(model, optimizer, loss_fn, dataloader, metrics, params, args):
    """Train the model on `num_steps` batches

    Args:
        model: (torch.nn.Module) the neural network
        optimizer: (torch.optim) optimizer for parameters of model
        loss_fn: a function that takes batch_output and batch_labels and computes the loss for the batch
        dataloader: (DataLoader) a torch.utils.data.DataLoader object that fetches training data
        metrics: (dict) a dictionary of functions that compute a metric using the output and labels of each batch
        params: (Params) hyperparameters
        num_steps: (int) number of batches to train on, each of size params.batch_size
    """

    # set model to training mode
    model.train()

    # summary for current training loop and a running average object for loss
    summ = []
    loss_avg = utils.RunningAverage()

    # Use tqdm for progress bar
    with tqdm(total=len(dataloader)) as t:
        for i, (train_batch, labels_batch) in enumerate(dataloader):
            train_batch = train_batch.to(args.device, non_blocking=params.cuda)
            labels_batch = labels_batch.to(args.device, non_blocking=params.cuda)

            # compute model output and loss
            output_batch = model(train_batch)
            loss = loss_fn(output_batch, labels_batch)

            # update weight
            optimizer.zero_grad()

            if params.fp16:
                with amp.scale_loss(loss, optimizer) as scaled_loss:
                    scaled_loss.backward()
            else:
                loss.backward()

            optimizer.step()

            # Evaluate summaries only once in a while
            if i % params.save_summary_steps == 0:
                # compute all metrics on this batch (Use predefine in Net.py)
                summary_batch = {metric: metrics[metric](output_batch, labels_batch) for metric in metrics}
                summary_batch['loss'] = loss.item()

                if params.distributed:
                    for k, v in summary_batch.items():
                        v = reduce_tensor(torch.tensor(v, device=args.device), args).item()
                        summary_batch[k] = v

                summ.append(summary_batch)

            # update the average loss
            loss_avg.update(loss.item())

            # del loss for saving memory
            del loss

            # update tqdm
            t.set_postfix(loss='{:05.3f}'.format(loss_avg()))
            t.update()

    # compute mean of all metrics in summary and logging
    torch.cuda.synchronize()

    metrics_mean = {metric: np.mean([x[metric]
                                     for x in summ]) for metric in summ[0]}
    metrics_string = " ; ".join("{}: {:05.3f}".format(k, v)
                                for k, v in metrics_mean.items())

    logging.info("- Train metrics: " + metrics_string)


def train_and_evaluate(model, train_dataloader, val_dataloader, optimizer, loss_fn, metrics, train_sampler, params,
                       args):
    """Train the model and evaluate every epoch.

    Args:
        model: (torch.nn.Module) the neural network
        train_dataloader: (DataLoader) a torch.utils.data.DataLoader object that fetches training data
        val_dataloader: (DataLoader) a torch.utils.data.DataLoader object that fetches validation data
        optimizer: (torch.optim) optimizer for parameters of model
        loss_fn: a function that takes batch_output and batch_labels and computes the loss for the batch
        metrics: (dict) a dictionary of functions that compute a metric using the output and labels of each batch
        params: (Params) hyperparameters
        model_dir: (string) directory containing config, weights and log
        restore_file: (string) optional- name of file to restore from (without its extension .pth.tar)
    """
    # reload weights from restore_file if specified
    if args.restore_file is not None:
        # Use a local scope to avoid dangling references
        restore_path = os.path.join(
            args.model_dir, args.restore_file + '.pth.tar')

        if os.path.isfile(restore_path):
            logging.info("Restoring parameters from {}".format(restore_path))
            utils.load_checkpoint(restore_path, args, model, optimizer)
        else:
            logging.info("=> no checkpoint found at '{}'".format(restore_path))

    # Use acc for early stopping
    best_val_acc = 0.0

    for epoch in range(params.num_epochs):

        # Set epoch for random sample
        if params.distributed:
            train_sampler.set_epoch(epoch)

        # Run one epoch
        logging.info("Epoch {}/{}".format(epoch + 1, params.num_epochs))

        # compute number of batches in one epoch (one full pass over the training set)
        train(model, optimizer, loss_fn, train_dataloader, metrics, params, args)

        # Evaluate for one epoch on validation set
        val_metrics = evaluate(model, loss_fn, val_dataloader, metrics, params, args)

        # Process on GPU#0
        if args.local_rank in [-1, 0]:

            val_acc = val_metrics['accuracy']
            is_best = val_acc >= best_val_acc

            # Save weights
            utils.save_checkpoint({'epoch': epoch + 1,
                                   'state_dict': model.state_dict(),
                                   'optim_dict': optimizer.state_dict()},
                                  is_best=is_best,
                                  checkpoint=args.model_dir)

            # If best_eval, best_save_path
            if is_best:
                logging.info("- Found new best accuracy")
                best_val_acc = val_acc

                # Save best val metrics in a json file in the model directory
                best_json_path = os.path.join(
                    args.model_dir, "metrics_val_best_weights.json")
                utils.save_dict_to_json(val_metrics, best_json_path)

            # Save latest val metrics in a json file in the model directory
            last_json_path = os.path.join(
                args.model_dir, "metrics_val_last_weights.json")
            utils.save_dict_to_json(val_metrics, last_json_path)


if __name__ == '__main__':

    # Load the parameters from json file
    args = parser.parse_args()
    json_path = os.path.join(args.model_dir, 'params.json')
    assert os.path.isfile(json_path), "No json configuration file found at {}".format(json_path)
    params = utils.Params(json_path)

    # Set the logger
    utils.set_logger(os.path.join(args.model_dir, 'train.log'))

    # Set random seed
    logging.info("Set random seed={}".format(params.seed))
    utils.set_seed(params)
    logging.info("- done.")

    # Set device
    device = None
    args.world_size = 1
    if params.cuda:
        device = torch.device('cuda')
        cudnn.benchmark = True  # Enable cudnn
    else:
        device = torch.device('cpu')
    if params.distributed and params.device_count > 1 and params.cuda:
        torch.cuda.set_device(args.local_rank)
        device = torch.device('cuda', args.local_rank)
        torch.distributed.init_process_group(backend="nccl")
        args.world_size = torch.distributed.get_world_size()

    args.device = device

    # Create the input data pipeline
    logging.info("Loading the datasets...")
    dataloaders, samplers = data_loader.fetch_dataloader(['train', 'val'], args.data_dir, params)
    train_dl = dataloaders['train']
    val_dl = dataloaders['val']
    train_sampler = samplers['train']
    # val_sp = samplers['val']
    logging.info("- done.")

    # Define the model and optimizer
    logging.info("Define model and optimizer...")
    model = net.Net(params).to(args.device)
    optimizer = optim.Adam(model.parameters(), lr=params.learning_rate)

    if params.sync_bn:
        logging.info("using apex synced BN")
        model = apex.parallel.convert_syncbn_model(model)

    if params.fp16:
        logging.info("using apex fp16, opt_level={}, keep_batchnorm_fp32={}".format(params.fp16_opt_level,
                                                                                    params.keep_batchnorm_fp32))
        # 'O1' enable bn32 default, disable explicitly
        if params.fp16_opt_level == 'O1':
            params.keep_batchnorm_fp32 = None
        model, optimizer = amp.initialize(model, optimizer, opt_level=params.fp16_opt_level,
                                          keep_batchnorm_fp32=params.keep_batchnorm_fp32)

    if params.distributed and params.device_count > 1 and params.cuda:
        logging.warning(
            "Process rank: %s, device: %s, n_gpu: %s, distributed training: %s",
            args.local_rank,
            device,
            params.device_count,
            bool(args.local_rank != -1),
        )
        model = DDP(model)

    logging.info("- done.")

    # fetch loss function and metrics
    loss_fn = net.loss_fn(args)
    metrics = net.metrics

    # Train the model
    logging.info("Starting training for {} epoch(s)".format(params.num_epochs))
    train_and_evaluate(model, train_dl, val_dl, optimizer, loss_fn, metrics, train_sampler, params, args)
