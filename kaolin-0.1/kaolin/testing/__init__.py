# Copyright (c) 2019, NVIDIA CORPORATION. All rights reserved.
# Kornia components:
# Copyright (C) 2017-2019, Arraiy, Inc., all rights reserved.
# Copyright (C) 2019-    , Open Source Vision Foundation, all rights reserved.
# Copyright (C) 2019-    , Kornia authors, all rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Testing specific utils
"""

import torch

# Borrowed from kornia
# https://github.com/arraiyopensource/kornia
# https://github.com/kornia/kornia/blob/master/kornia/testing/__init__.py
def tensor_to_gradcheck_var(tensor, dtype=torch.float64, requires_grad=True):
    """Makes input tensors gradcheck-compatible (i.e., float64, and
       requires_grad = True).
    """

    assert torch.is_tensor(tensor), type(tensor)
    return tensor.requires_grad_(requires_grad).type(dtype)
