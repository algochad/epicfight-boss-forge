"""Runner: render the textured, posed character. Args (env BFK_ARGS) are documented in bfk.view.run."""
import bfk
from bfk import view

bfk.emit(view.run(bfk.get_args()))
