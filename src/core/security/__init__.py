"""
Security subsystem for LJS.

This package centralizes filesystem, command, confirmation, and audit guards so
agent tools and category workflows cannot bypass safety checks by accident.
"""

from src.core.security.audit import SecurityAuditLogger
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.security.confirmation import SecurityConfirmationService
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError

__all__ = [
    "CommandPolicy",
    "CommandPolicyError",
    "SafePathResolver",
    "SecurityAuditLogger",
    "SecurityConfirmationService",
    "SecurityPolicyError",
]
