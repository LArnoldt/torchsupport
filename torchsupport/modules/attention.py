import torch
import torch.nn as nn
import torch.nn.functional as func
import sys

class AttentionBranch(nn.Module):
  def __init__(self, N, branches, in_channels, preprocess=None, activation=func.tanh):
    """Pixel-wise branch selection layer using attention.

    Args:
      N (int): dimensionality of convolutions.
      branches (iterable nn.Module): neural network branches to choose from.
      in_channels (int): number of input channels.
      preprocess (nn.Module): module performing feature preprocessing for attention.
      activation (nn.Module): activation function for attention computation. 
    """
    super(AttentionBranch, self).__init__()
    self.is_module = False
    if isinstance(branches, nn.Module):
      self.branches = branches
      self.is_module = True
    else:
      self.branches = nn.ModuleList(branches)
    branch_size = len(self.branches)
    self.attention_preprocess = preprocess
    if self.attention_preprocess == None:
      self.attention_preprocess = nn.__dict__[f"Conv{N}d"](in_channels, in_channels, 3)
    self.attention_activation = activation
    self.attention_calculation = nn.__dict__[f"Conv{N}d"](in_channels, branch_size, 1)

  def forward(self, input):
    if self.is_module:
      branches = self.branches(input)
    else:
      branches = torch.cat([
        branch(input)
        for branch in self.branches
      ], dim=1)
    attention = self.attention_preprocess(input)
    attention = self.attention_activation(attention)
    attention = self.attention_calculation(attention)
    out = (attention.unsqueeze(1) * branches.unsqueeze(2)).sum(dim=1)
    return out

# Generate variants:
for dim in range(1, 4):
  def _generating_function_outer(N):
    def _inner(branches, in_channels, preprocess=None, activation=func.tanh):
      """See `AttentionBranch`."""
      return AttentionBranch(N, branches, in_channels, preprocess=preprocess, activation=activation)
  setattr(sys.modules[__name__], f"AttentionBranch{dim}d", _generating_function_outer(dim))

class GuidedAttention(nn.Module):
  def __init__(self, N, in_channels, out_channels, hidden=32,
               inner_activation=func.relu, outer_activation=func.tanh,
               reduce=True):
    """Pixel-wise attention gated by a guide image.

    Args:
      N (int): dimensionality of convolutions.
      in_channels (int): number of input channels.
      out_channels (int): number of attention heads.
      hidden (int): number of hidden channels.
      inner_activation (nn.Module): activation on guide and input sum.
      outer_activation (nn.Module): activation on attention.
      reduce (bool): reduce or concatenate the results of the attention heads.
    """
    super(GuidedAttention, self).__init__()
    self.input_embedding = nn.__dict__[f"Conv{N}d"](in_channels, hidden, 1)
    self.guide_embedding = nn.__dict__[f"Conv{N}d"](in_channels, hidden, 1)
    self.attention_computation = nn.__dict__[f"Conv{N}d"](hidden, out_channels, 1)
    self.inner_activation = inner_activation
    self.outer_activation = outer_activation
    self.reduce = reduce

  def forward(self, input, guide):
    ie = self.input_embedding(input)
    ge = self.guide_embedding(guide)
    total = self.inner_activation(ie + ge)
    attention = self.outer_activation(self.attention_computation(total))
    if self.reduce:
      out = (attention.unsqueeze(1) * input.unsqueeze(2)).sum(dim=1)
    else:
      asize = attention.size()
      out = (attention.unsqueeze(1) * input.unsqueeze(2)).reshape(
        asize[0], asize[1] * asize[2], *asize[3:]
      )
    return out

# Generate variants:
for dim in range(1, 4):
  def _generating_function_outer(N):
    def _inner(in_channels, out_channels=1, hidden=32,
               inner_activation=func.relu, outer_activation=func.tanh,
               reduce=False):
      """See `GuidedAttention`."""
      return GuidedAttention(N, in_channels, out_channels, hidden=hidden,
                             inner_activation=inner_activation, outer_activation=outer_activation,
                             reduce=reduce)
  setattr(sys.modules[__name__], f"GuidedAttention{dim}d", _generating_function_outer(dim))
