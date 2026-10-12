"""Runner: build the EF biped armature and save it. Args (env BFK_ARGS): out, name, reset."""
import bfk
from bfk import rig

bfk.emit(rig.run(bfk.get_args()))
