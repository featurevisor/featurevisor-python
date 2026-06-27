from .child import FeaturevisorChildInstance
from .modules import FeaturevisorModule, ModulesManager
from .instance import FeaturevisorInstance, create_instance
from .logger import Logger

createInstance = create_instance
Featurevisor = FeaturevisorInstance

__all__ = [
    "Featurevisor",
    "FeaturevisorChildInstance",
    "FeaturevisorInstance",
    "FeaturevisorModule",
    "ModulesManager",
    "Logger",
    "create_instance",
    "createInstance",
]
