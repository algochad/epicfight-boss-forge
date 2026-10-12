"""Runner: export an EF mesh bundle. Args (env BFK_ARGS) are documented in bfk.efio.run_export."""
import bfk
from bfk import efio

bfk.emit(efio.run_export(bfk.get_args()))
