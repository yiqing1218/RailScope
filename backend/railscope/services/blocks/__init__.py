from ...domain import BlockEdge, BlockSection
from ...repository import RailRepository


def create_virtual_blocks(repo: RailRepository) -> list[BlockSection]:
    """One virtual block per rail edge is deterministic and preserves an upgrade path to real blocks."""
    blocks = []
    for edge in repo.edges.values():
        if edge.mode != "rail":
            continue
        block = BlockSection(f"block-{edge.id}", f"Block {edge.id}", edge.from_node_id, edge.to_node_id, edge.length_m)
        if block.id in repo.blocks and repo.blocks[block.id] != block:
            raise ValueError(f'虚拟闭塞区间与已有定义冲突：{block.id}')
        blocks.append(block)
    generated = {block.id for block in blocks}
    repo.blocks.update((block.id, block) for block in blocks)
    repo.block_edges = [member for member in repo.block_edges if member.block_id not in generated] + [
        BlockEdge(block.id, block.id.removeprefix('block-'), 1, True) for block in blocks]
    return blocks
