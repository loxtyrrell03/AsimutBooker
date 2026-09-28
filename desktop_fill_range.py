"""PC fill requests use the shared operation owner and worker."""
from desktop_room_now import DesktopRoomNow
from fill_range import validate_choices


class DesktopFillRange(DesktopRoomNow):
    action = 'fill_range'
    validate = staticmethod(validate_choices)
