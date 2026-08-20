class BSDCError(Exception):
    """Base Exception for all BSDC Engine Errors."""
    pass


class SharePointAuthError(BSDCError):
    """Raised when SharePoint authentication or session expires."""
    pass


class MappingParseError(BSDCError):
    """Raised when mapping Excel file has invalid format or structure."""
    pass


class RuleResolutionError(BSDCError):
    """Raised when DSL or transformation rule cannot be resolved."""
    pass


class ConversionError(BSDCError):
    """Raised when Excel to CSV conversion fails."""
    pass


class DatabaseError(BSDCError):
    """Raised when SQLite operations fail."""
    pass