"""Airport adapter over the shared source-backed outline/POI index."""
from functools import partial
import university_store as sites

PROFILE = sites.AIRPORT
database_path = partial(sites.database_path, profile=PROFILE)
install_index = partial(sites.install_index, profile=PROFILE)
ensure_index = partial(sites.ensure_index, profile=PROFILE)
build_index = partial(sites.build_index, profile=PROFILE)
write_index = partial(sites.write_index, profile=PROFILE)
viewport = partial(sites.viewport, profile=PROFILE)
