class FriendlyException(Exception):
    """Anything the mill owner can fix. message = what went wrong, fix = what to do. Plain English."""
    def __init__(self, message: str, fix: str):
        super().__init__(message)
        self.message = message
        self.fix = fix
