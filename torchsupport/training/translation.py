import torch

from torchsupport.data.io import make_differentiable
from torchsupport.training.gan import (
  RothGANTraining, AbstractGANTraining, GANTraining
)

class PairedGANTraining(RothGANTraining):
  def sample(self, data):
    noise = super().sample(data)
    return noise, data[0]

class CycleGANTraining(RothGANTraining):
  def __init__(self, generators, discriminators, data, gamma=10, **kwargs):
    self.fw = ...
    self.rv = ...
    self.fw_discriminator = ...
    self.rv_discriminator = ...
    self.discriminator = ...
    AbstractGANTraining.__init__(
      {"fw": generators[0], "rv": generators[1]},
      {"fw_discriminator": discriminators[0], "rv_discriminator": discriminators[1]},
      data, **kwargs
    )
    self.generator = self.fw

    self.gamma = gamma

  def set_discriminator(self, disc):
    self.discriminator = disc

  def cycle_loss(self, data, cycled):
    l1 = (data - cycled).view(data.size(0), -1).norm(p=1)
    return l1.mean()

  def generator_step_loss(self, data, translated, cycled):
    self.set_discriminator(self.fw_discriminator)
    loss_fw = self.generator_loss(data[0], translated[0])
    loss_cycle_fw = self.cycle_loss(data[0], cycled[0])

    self.set_discriminator(self.rv_discriminator)
    loss_rv = self.generator_loss(data[1], translated[1])
    loss_cycle_rv = self.cycle_loss(data[1], cycled[1])

    loss_gan = loss_fw + loss_rv
    loss_cycle = loss_cycle_fw + loss_cycle_rv

    self.current_losses["cycle"] = float(loss_cycle)
    self.current_losses["gan"] = float(loss_gan)

    return loss_gan + self.gamma * loss_cycle

  def discriminator_step_loss(self, translated, data, translated_result, real_result):
    self.set_discriminator(self.fw_discriminator)
    loss_fw, out_fw = self.discriminator_loss(
      translated[0], data[0], translated_result[0], real_result[0]
    )
    self.current_losses["fw_discriminator"] = float(loss_fw)

    self.set_discriminator(self.rv_discriminator)
    loss_rv, out_rv = self.discriminator_loss(
      translated[1], data[1], translated_result[1], real_result[1]
    )
    self.current_losses["rv_discriminator"] = float(loss_rv)

    loss = loss_fw + loss_rv
    out = (out_fw, out_rv)

    self.current_losses["discriminator"] = float(loss)

    return loss, out

  def run_generator(self, data):
    translated_fw = self.fw(self.sample(data), data[0])
    cycled_fw = self.rv(self.sample(data), translated_fw)

    translated_rv = self.rv(self.sample(data), data[1])
    cycled_rv = self.fw(self.sample(data), translated_rv)

    translated = (translated_fw, translated_rv)
    cycled = (cycled_fw, cycled_rv)

    return data, translated, cycled

  def run_discriminator(self, data):
    with torch.no_grad():
      _, (fake_fw, fake_rv), _ = self.run_generator(data)
    make_differentiable((fake_fw, fake_rv))
    make_differentiable(data)
    real_result_fw = self.fw_discriminator(data[1])
    fake_result_fw = self.fw_discriminator(fake_fw)
    real_result_rv = self.rv_discriminator(data[0])
    fake_result_rv = self.rv_discriminator(fake_rv)

    real_result = (real_result_fw, real_result_rv)
    fake_result = (fake_result_fw, fake_result_rv)
    fake_batch = fake_fw, fake_rv

    return fake_batch, data, fake_result, real_result
