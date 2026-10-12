"""Runner: import an EF mesh bundle. Args (env BFK_ARGS) are documented in bfk.efio.run_import."""
import bfk
from bfk import efio

bfk.emit(efio.run_import(bfk.get_args()))
