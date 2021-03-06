import os

import torch
from PIL import Image  # PILs图像处理库
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms as transforms  # 常用的图像变换，如crop操作等

# borrowed from http://pytorch.org/tutorials/advanced/neural_style_tutorial.html
# and http://pytorch.org/tutorials/beginner/data_loading_tutorial.html
# define a training image loader that specifies transforms on images. See documentation for more details.
train_transformer = transforms.Compose([  # transforms变换能够用Compose串联组合起
    # randomly flip image horizontally，概率随机水平翻折PIL图片
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor()  # 将PIL Image或numpy.ndarray转化成张量。
])  # transform it into a torch tensor

# loader for evaluation, no horizontal flip
eval_transformer = transforms.Compose([
    transforms.ToTensor()
])  # transform it into a torch tensor


class SIGNSDataset(Dataset):  # 继承torch中的dataset类，继承dataset类必须重写__len__与__getitem__两个方法
    """
    A standard PyTorch definition of Dataset which defines the functions __len__ and __getitem__.
    """

    def __init__(self, data_dir, transform):  # 创建实例时必须传入与__init__匹配的参数
        """
        Store the filenames of the jpgs to use. Specifies transforms to apply on images.

        Args:
            data_dir: (string) directory containing the dataset
            transform: (torchvision.transforms) transformation to apply on image
        """
        self.filenames = os.listdir(data_dir)  # 返回文件夹中包含的文件或文件夹的名字的列表
        self.filenames = [os.path.join(
            data_dir, f) for f in self.filenames if f.endswith('.jpg')]  # 返回.jpg文件的路径
        # self.labels需要提前定义jpg文件名字的第一个数字是label，如245.jpg的label就是2
        self.labels = [int(os.path.split(filename)[-1][0])
                       for filename in self.filenames]
        self.transform = transform

    def __len__(self):  # 返回数据长度
        # return size of dataset
        return len(self.filenames)

    def __getitem__(self, idx):  # 返回训练数据，image与label
        """
        Fetch index idx image and labels from dataset. Perform transforms on image.

        Args:
            idx: (int) index in [0, 1, ..., size_of_dataset-1]

        Returns:
            image: (Tensor) transformed image
            label: (int) corresponding label of image
        """
        image = Image.open(self.filenames[idx])  # PIL image
        image = self.transform(image)  # image转成tensor形式
        return image, self.labels[idx]


def fetch_dataloader(types, data_dir, params):
    """
    Fetches the DataLoader object for each type in types from data_dir.
    获取对应于type的数据集
    Args:
        types: (list) has one or more of 'train', 'val', 'test' depending on which data is required
        data_dir: (string) directory containing the dataset
        params: (Params) hyperparameters

    Returns:
        data: (dict) contains the DataLoader object for each type in types
    """
    dataloaders = {}
    samplers = {}

    for split in ['train', 'val', 'test']:
        if split in types:
            path = os.path.join(data_dir, "{}".format(split))  # 对应type数据集的存储路径

            # use the train_transformer if training data, else use eval_transformer without random flip
            # take care of 'pin_memory' and 'num_workers'
            if split == 'train':
                train_set = SIGNSDataset(path, train_transformer)
                sampler = None
                if params.distributed:  # 如果采用数据多机多卡的数据并行，DistributedSampler()为每一个子进程分出一部分数据集，避免不同进程之间数据重复
                    sampler = torch.utils.data.distributed.DistributedSampler(
                        train_set)
                dl = DataLoader(train_set, batch_size=params.batch_size_pre_gpu, shuffle=(sampler is None),
                                num_workers=params.num_workers, pin_memory=params.cuda, sampler=sampler)  # 从数据中每次抽出batch size个样本
            else:
                val_set = SIGNSDataset(path, eval_transformer)
                sampler = None
                if params.distributed:
                    sampler = torch.utils.data.distributed.DistributedSampler(
                        val_set)
                dl = DataLoader(val_set, batch_size=params.batch_size_pre_gpu, shuffle=False,
                                num_workers=params.num_workers, pin_memory=params.cuda, sampler=sampler)

            dataloaders[split] = dl
            samplers[split] = sampler

    return dataloaders, samplers
