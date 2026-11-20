"""Lazy production adapter; the synthetic demonstration needs no customer database."""
from .tools import TOOLS
class Data:
    def __new__(cls,scope,factory=None):
        from .integrations.pokerfill.data import Data as Source
        return Source(scope,**({'factory':factory} if factory is not None else {}))
def __getattr__(name):
    if name in ('DiagnosticDatabase','reader'):
        from .integrations.pokerfill import data
        return getattr(data,name)
    raise AttributeError(name)
