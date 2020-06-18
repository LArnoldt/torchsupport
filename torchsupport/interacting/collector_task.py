from collections import namedtuple

import torch
import torch.multiprocessing as mp

class AbstractCollector:
  def pull_changes(self):
    raise NotImplementedError("Abstract.")

  def push_changes(self):
    raise NotImplementedError("Abstract.")

  def step(self, *args, **kwargs):
    raise NotImplementedError("Abstract.")

  def process_step(self, step_result):
    raise NotImplementedError("Abstract.")

  def process_trajectory(self, results):
    raise NotImplementedError("Abstract.")

  def initial_state(self):
    raise NotImplementedError("Abstract.")

  def schema(self):
    raise NotImplementedError("Abstract.")

  def initialize(self):
    pass

  def compute_statistics(self, trajectory):
    return None

  def sample_trajectory(self):
    self.pull_changes()
    self.initialize()
    state = self.initial_state()
    trajectory = []
    done = False
    while not done:
      state = self.step(state)
      done = state.done
      trajectory.append(self.process_step(state))
    result = self.process_trajectory(trajectory)
    self.push_changes()
    return result

class EnvironmentCollector(AbstractCollector):
  data_type = namedtuple("Data", [
    "initial_state", "final_state",
    "logits", "outputs", "rewards",
    "done", "returns", "action"
  ])
  stat_type = namedtuple("Stat", [
    "total", "length"
  ])

  def __init__(self, environment, policy, discount=0.9):
    self.discount = discount
    self.environment = environment
    self.policy = policy.move()

  def pull_changes(self):
    self.policy.pull()
    self.environment.pull_changes()

  def push_changes(self):
    pass # by default, we request no changes.

  def initialize(self):
    self.environment.reset()

  def initial_state(self):
    return self.data_type(
      initial_state=self.environment.observe(),
      final_state=self.environment.observe(),
      logits=None, action=None, rewards=None,
      outputs=None, done=False, returns=None
    )

  def step(self, state):
    with torch.no_grad():
      initial = self.environment.observe()
      action, logits, outputs = self.policy(initial, state.outputs)
      rewards = self.environment.act(action)
      final = self.environment.observe()
      done = self.environment.is_done()
      done = torch.tensor([done])
      action = action.unsqueeze(0)
    return self.data_type(
      initial_state=initial, final_state=final,
      logits=logits, action=action, rewards=rewards,
      outputs=outputs, done=done,
      returns=None
    )

  def schema(self):
    env_schema = self.environment.schema()
    policy_schema = self.policy.schema()
    return self.data_type(
      initial_state=env_schema.state, final_state=env_schema.state,
      logits=policy_schema.logits, action=env_schema.action,
      rewards=env_schema.rewards, returns=env_schema.rewards,
      outputs=policy_schema.outputs, done=env_schema.done
    )

  def process_step(self, state):
    return state # by default, we do not postprocess observations.

  def process_trajectory(self, trajectory):
    result = []
    discounted = 0
    for step in reversed(trajectory):
      discounted = step.rewards + self.discount * discounted
      result = [step._replace(returns=discounted)] + result
    return result

  def compute_statistics(self, trajectory):
    total = sum(map(lambda x: x.rewards, trajectory))
    length = len(trajectory)
    return self.stat_type(total=total, length=length)

class EnergyCollector(AbstractCollector):
  data_type = namedtuple("Data", [
    "initial_state", "final_state",
    "initial_energy", "final_energy"
  ])
  stat_type = namedtuple("Stat", [
    "energy"
  ])

  def __init__(self, energy, integrator, batch_size=32):
    self.integrator = integrator
    self.energy = energy.move()
    self.batch_size = 32
    self.batch = None

  def pull_changes(self):
    self.energy.pull()

  def push_changes(self):
    pass # by default, we request no changes.

  def initialize(self):
    pass

  def initial_state(self):
    if self.batch is None:
      self.batch = self.energy.prepare().detach()
    batch = self.batch
    batch_energy = self.energy(batch).detach()
    return self.data_type(
      initial_state=batch,
      final_state=batch,
      initial_energy=batch_energy,
      final_energy=batch_energy
    )

  def step(self, state):
    batch = state.final_energy
    energy = state.final_energy
    batch = self.energy.reset(batch, energy).detach()
    new_batch = self.integrator(self.energy, batch).detach()
    new_energy = self.energy(new_batch).detach()

    return self.data_type(
      initial_state=batch, final_state=new_batch,
      initial_energy=energy, final_energy=new_energy
    )

  def schema(self):
    schema = self.energy.schema()
    return self.data_type(
      initial_state=schema.state, final_state=schema.state,
      initial_energy=schema.energy, final_energy=schema.energy
    )

  def compute_statistics(self, trajectory):
    energy = sum(map(lambda x: x.final_energy, trajectory))
    length = len(trajectory)
    return self.stat_type(energy=energy / length)

  def sample_trajectory(self):
    self.pull_changes()
    self.initialize()
    state = self.initial_state()
    state = self.step(state)
    self.batch = state
    self.push_changes()
    trajectory = [
      self.data_type(
        initial_state=initial, final_state=final,
        initial_energy=E_i, final_energy=E_f
      )
      for initial, final, E_i, E_f in zip(*state)
    ]
    return trajectory
