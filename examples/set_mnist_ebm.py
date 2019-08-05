import random

import torch
import torch.nn as nn
import torch.nn.functional as func
from torch.nn.utils import spectral_norm
from torch.utils.data import Dataset
from torch.distributions import Normal

from torchvision.datasets import MNIST
from torchvision.transforms import ToTensor

from torchsupport.modules.basic import MLP
from torchsupport.modules.residual import ResNetBlock2d
from torchsupport.training.energy import SetVAETraining, Langevin

def normalize(image):
  return (image - image.min()) / (image.max() - image.min())

class EnergyDataset(Dataset):
  def __init__(self, data):
    self.data = data

  def __getitem__(self, index):
    data, label_index = self.data[index]
    # data = data + 0.05 * torch.rand_like(data)
    label = torch.zeros(10)
    label[label_index] = 1
    return data, label

  def __len__(self):
    return len(self.data)

class MNISTSet(EnergyDataset):
  def __init__(self, data, size=5):
    super().__init__(data)
    self.size = size

  def __getitem__(self, index):
    data = []
    label = random.randrange(10)
    for idx in range(self.size):
      d, l = super().__getitem__(random.randrange(len(self)))
      while l[label] < 1.0:
        d, l = super().__getitem__(random.randrange(len(self)))
      data.append(d.unsqueeze(0))
    data = torch.cat(data, dim=0)
    return data, data

class SingleEncoder(nn.Module):
  def __init__(self, latents=32):
    super(SingleEncoder, self).__init__()
    self.block = MLP(28 * 28, latents, hidden_size=128, depth=2, batch_norm=False, normalization=spectral_norm)

  def forward(self, inputs):
    return self.block(inputs)

class Encoder(nn.Module):
  def __init__(self, single, size=5, latents=16):
    super(Encoder, self).__init__()
    self.size = size
    self.single = single
    self.weight = spectral_norm(nn.Linear(32, 1))
    self.mean = spectral_norm(nn.Linear(32, latents))
    self.logvar = spectral_norm(nn.Linear(32, latents))

  def forward(self, inputs):
    inputs = inputs.view(-1, 28 * 28)
    out = self.single(inputs)
    weights = self.weight(out)
    out = out.view(-1, self.size, 32)
    weights = weights.view(-1, self.size, 1).softmax(dim=1)
    pool = out.mean(dim=1)#(weights * out).sum(dim=1)
    return self.mean(pool), self.logvar(pool)

class Energy(nn.Module):
  def __init__(self, sample=True):
    super(Energy, self).__init__()
    self.sample = sample

    self.input = SingleEncoder()
    self.condition = Encoder(self.input)
    self.input_process = spectral_norm(nn.Linear(32, 64))
    self.postprocess = spectral_norm(nn.Linear(16, 64))
    self.combine = MLP(128, 1, hidden_size=64, depth=3, batch_norm=False, normalization=spectral_norm)

  def forward(self, image, condition):
    image = image.view(-1, 28 * 28)
    out = self.input_process(self.input(image))
    mean, logvar = self.condition(condition)
    distribution = Normal(mean, torch.exp(0.5 * logvar))
    sample = distribution.rsample()
    cond = self.postprocess(sample)
    cond = torch.repeat_interleave(cond, 5, dim=0)
    result = self.combine(torch.cat((out, cond), dim=1))
    return result, (mean, logvar)

class MNISTSetTraining(SetVAETraining):
  def each_generate(self, data, *args):
    ref = args[0]
    samples = [sample for sample in ref.contiguous().view(-1, 1, 28, 28)[:10]]
    samples = torch.cat(samples, dim=-1)
    self.writer.add_image("reference", samples, self.step_id)

    samples = [sample for sample in data.view(-1, 1, 28, 28)[:10]]
    samples = torch.cat(samples, dim=-1)
    self.writer.add_image("samples", samples, self.step_id)

if __name__ == "__main__":
  mnist = MNIST("examples/", download=False, transform=ToTensor())
  data = MNISTSet(mnist)

  energy = Energy()
  integrator = Langevin(rate=100, steps=30, max_norm=None)
  
  training = MNISTSetTraining(
    energy, data,
    network_name="set-mnist-ebm",
    device="cpu",
    integrator=integrator,
    batch_size=12,
    max_epochs=1000,
    verbose=True
  )

  training.train()
