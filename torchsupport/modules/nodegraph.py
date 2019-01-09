import torch
import torch.nn as nn
import torch.nn.functional as func
from copy import copy, deepcopy

class NodeGraphTensor(object):
  def __init__(self, graphdesc=None):
    self.is_subgraph = False
    self.offset = 0

    if graphdesc == None:
      self.num_graphs = 1
      self.graph_nodes = [0]
      self._adjacency = []

      self._node_tensor = torch.tensor([])
    else:
      self.num_graphs = graphdesc["num_graphs"]
      self.graph_nodes = graphdesc["graph_nodes"]
      self._adjacency = graphdesc["adjacency"]

      self._node_tensor = graphdesc["node_tensor"]

  @property
  def adjacency(self):
    if self.is_subgraph:
      start = 0 if self.offset == 0 else self.nodes_including(self.offset - 1)
      stop = self.nodes_including(self.offset)
      return self._adjacency[start:stop]
    else:
      return self._adjacency

  @property
  def node_tensor(self):
    if self.is_subgraph:
      start = 0 if self.offset == 0 else self.nodes_including(self.offset - 1)
      stop = self.nodes_including(self.offset)
      return self._node_tensor[start:stop]
    else:
      return self._adjacency

  @node_tensor.setter
  def node_tensor(self, value):
    if self.is_subgraph:
      start = 0 if self.offset == 0 else self.nodes_including(self.offset - 1)
      stop = self.nodes_including(self.offset)
      self._node_tensor[start:stop] = value
    else:
      self._node_tensor = value

  def nodes_including(self, graph_index):
    return sum(self.graph_nodes[:graph_index+1])

  def add_node(self, node_tensor):
    assert (self.num_graphs == 1)
    self.graph_nodes[self.offset] += 1
    self._adjacency.append([])
    self._node_tensor = torch.cat(
      (self._node_tensor[:self.nodes_including(self.offset)],
       node_tensor.unsqueeze(0).unsqueeze(0),
       self._node_tensor[self.nodes_including(self.offset):]), 0)
    return self._node_tensor.size(0) - 1

  def add_edge(self, source, target):
    self.adjacency[source].append(target)
    self.adjacency[target].append(source)
    return len(self.adjacency[source]) - 1

  def __getitem__(self, idx):
    assert (self.num_graphs > 1)
    # TODO
    return None

  def append(self, graph_tensor):
    assert(self.offset == 0)
    self.num_graphs += graph_tensor.num_graphs
    self.adjacency += list(map(
      lambda x: x + len(self._adjacency), graph_tensor.adjacency))
    self.graph_nodes += graph_tensor.graph_nodes
    self._node_tensor = torch.cat((self.node_tensor, graph_tensor.node_tensor), 0)

class PartitionedNodeGraphTensor(NodeGraphTensor):
  def __init__(self, graphdesc=None):
    super(PartitionedNodeGraphTensor, self).__init__(graphdesc=graphdesc)
    self.partition_view = None
    if graphdesc == None:
      self.partition = { None: [] }
    else:
      self.partition = graphdesc["partition"]

  def none(self):
    view = copy(self)
    view.partition_view = 'none'
    return view

  def all(self):
    view = copy(self)
    view.partition_view = None
    return view
  
  def add_kind(self, name):
    self.partition[name] = []
    def _function():
      view = copy(self)
      view.partition_view = name
      return view
    self.__dict__[name] = _function
    return self.partition[name]

  def add_node(self, node_tensor, kind=None):
    node = super(PartitionedNodeGraphTensor, self).add_node(node_tensor)
    self.partition[kind].append(node)
    return node

  def append(self, graph_tensor):
    for kind in self.partition:
      self.partition[kind] += list(map(
        lambda x: x + len(self._adjacency), graph_tensor.partition[kind]))
    super(self, PartitionedNodeGraphTensor).append(graph_tensor)

def batch_graphs(graphs):
  result = deepcopy(graph[0])
  for idx in range(1, len(graphs)):
    result.append(graphs[idx])
  return result

class AllNodes(nn.Module):
  def __init__(self, node_update):
    super(AllNodes, self).__init__()
    self.node_update = node_update

  def forward(self, graph):
    if isinstance(graph, PartitionedNodeGraphTensor) and graph.partition_view != None:
      partition = graph.partition[graph.partition_view]
      graph._node_tensor[partition, :] = self.node_update(graph._node_tensor[partition, :])
    else:
      graph._node_tensor = self.node_update(graph._node_tensor)
    return graph

def LinearOnNodes(insize, outsize):
  lin = nn.Linear(insize, outsize)
  def mod(x):
    x = x.view(x.size()[0], -1)
    x = lin(x)
    x = x.unsqueeze(1)
    return x
  return AllNodes(mod)

def standard_node_traversal(depth):
  def function(graph, entity, d=depth):
    if d == 0:
        return [entity]
    else:
      nodes = [entity]
      edges = graph.adjacency[entity]
      nodes += edges
      for new_node in edges:
        if new_node != entity:
          new_nodes = function(
            graph, new_node, d - 1
          )
          nodes += new_nodes
      nodes = list(set(nodes))
      return nodes
  return function

class NodeGraphNeighbourhood(nn.Module):
  def __init__(self, reducer, traversal=standard_node_traversal(1), order=None):
    super(NodeGraphNeighbourhood, self).__init__()
    self.reducer = reducer
    self.traversal = traversal
    self.order = order

  def forward(self, graph, include_self=True):
    full_nodes = []
    partition = range(graph._node_tensor.size(0))
    if isinstance(graph, PartitionedNodeGraphTensor) and graph.partition_view != None:
      partition = graph.partition[graph.partition_view]
    for node in partition:
      nodes = self.traversal(graph, node)
      if self.order != None:
        nodes = self.order(graph, nodes)
      full_nodes.append(nodes)
    reduced_nodes = self.reducer(graph, full_nodes)
    graph._node_tensor = torch.cat((graph._node_tensor, reduced_nodes), 1)
    return graph

def _node_neighbourhood_attention(att):
  def reducer(graph, nodes):
    new_node_tensor = torch.zeros_like(graph._node_tensor)
    for idx, node in enumerate(nodes):
      local_tensor = graph._node_tensor[node]
      attention = att(torch.cat((local_tensor, graph._node_tensor[idx])))
      reduced = torch.Tensor.sum(attention * local_tensor, dim=1)
      new_node_tensor[idx] = reduced if isinstance(reduced, torch.Tensor) else reduced[0]
    return new_node_tensor
  return reducer

def NodeNeighbourhoodAttention(attention, traversal=standard_node_traversal(1)):
  return NodeGraphNeighbourhood(
    _node_neighbourhood_attention(attention),
    traversal=traversal
  )

def neighbourhood_to_adjacency(neighbourhood):
  size = torch.Size([len(neighbourhood), len(neighbourhood)])
  indices = []
  for idx, nodes in enumerate(neighbourhood):
    for node in nodes:
      indices.append([idx, node])
      indices.append([node, idx])
  indices = torch.Tensor(list(set(indices)))
  values = torch.ones(indices.size(0))
  return torch.sparse_coo_tensor(indices, values, size)

def _node_neighbourhood_sparse_attention(embedding, att, att_p):
  def reducer(graph, nodes):
    embedding = embedding(graph._node_tensor)
    local_attention = att.dot(embedding)
    neighbour_attention = att_p.dot(embedding)
    adjacency = neighbourhood_to_adjacency(nodes)
    new_node_tensor = local_attention + torch.spmm(adjacency, neighbour_attention)
    return new_node_tensor
  return reducer

def NodeNeighbourhoodSparseAttention(size, traversal=standard_node_traversal(1)):
  embedding = nn.Linear(size, size)
  att = torch.randn(size, requires_grad=True)
  att_p = torch.randn(size, requires_grad=True)
  return NodeGraphNeighbourhood(
    _node_neighbourhood_sparse_attention(embedding, att, att_p),
    traversal=traversal
  )

def _node_neighbourhood_dot_attention(embedding, att, att_p):
  def reducer(graph, nodes):
    embedding = embedding(graph._node_tensor)
    local_attention = att.dot(embedding)
    neighbour_attention = att_p.dot(embedding)
    for idx, node in enumerate(nodes):
      reduced = torch.Tensor.sum((local_attention[idx] + neighbour_attention[node]) * graph.node_tensor[node], dim=1)
      new_node_tensor[idx] = reduced if isinstance(reduced, torch.Tensor) else reduced[0]
    return new_node_tensor
  return reducer

def NodeNeighbourhoodDotAttention(size, traversal=standard_node_traversal(1)):
  embedding = nn.Linear(size, size)
  att = torch.randn(size, requires_grad=True)
  att_p = torch.randn(size, requires_grad=True)
  return NodeGraphNeighbourhood(
    _node_neighbourhood_dot_attention(embedding, att, att_p),
    traversal=traversal
  )

def _node_neighbourhood_reducer(red):
  def reducer(graph, nodes):
    new_node_tensor = torch.zeros_like(graph.node_tensor)
    for idx, node in enumerate(nodes):
      reduced = red(graph.node_tensor[node], dim=1)
      new_node_tensor[idx] = reduced if isinstance(reduced, torch.Tensor) else reduced[0]
    return new_node_tensor
  return reducer

def NodeNeighbourhoodMean(traversal=standard_node_traversal(1)):
  return NodeGraphNeighbourhood(
    _node_neighbourhood_reducer(torch.Tensor.mean),
    traversal=traversal
  )

def NodeNeighbourhoodSum(traversal=standard_node_traversal(1)):
  return NodeGraphNeighbourhood(
    _node_neighbourhood_reducer(torch.Tensor.sum),
    traversal=traversal
  )

def NodeNeighbourhoodMax(traversal=standard_node_traversal(1)):
  return NodeGraphNeighbourhood(
    _node_neighbourhood_reducer(torch.Tensor.max),
    traversal=traversal
  )

def NodeNeighbourhoodMin(traversal=standard_node_traversal(1)):
  return NodeGraphNeighbourhood(
    _node_neighbourhood_reducer(torch.Tensor.min),
    traversal=traversal
  )
