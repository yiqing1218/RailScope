from ...domain import BlockEdge, BlockSection
from ...repository import RailRepository


def create_virtual_blocks(repo: RailRepository) -> list[BlockSection]:
    """One virtual block per rail edge is deterministic and preserves an upgrade path to real blocks."""
    blocks = []
    for edge in repo.edges.values():
        if edge.mode != "rail":
            continue
        block = BlockSection(f"block-{edge.id}", f"Block {edge.id}", edge.from_node_id, edge.to_node_id, edge.length_m)
        repo.blocks[block.id] = block
        repo.block_edges.append(BlockEdge(block.id, edge.id, 1, True))
        blocks.append(block)
    return blocks
