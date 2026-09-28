"""macOS Calendar, through EventKit (the official API, which also sees recurring events).

Reads the events of a day or of the week; creates an event, always after a spoken
confirmation (see safety.calendar_add). Nothing is ever modified or deleted.
Asks once for full Calendar access (text in Info.plist: NSCalendarsFullAccessUsageDescription).
"""

import datetime as dt
import threading

import objc

# Signature of the access request's completion block: void (^)(BOOL granted, NSError *error)
objc.registerMetaDataForSelector(b"EKEventStore", b"requestFullAccessToEventsWithCompletion:", {
    "arguments": {2: {"callable": {"retval": {"type": b"v"},
                                   "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"}, 2: {"type": b"@"}}}}}})
objc.loadBundle("EventKit", {}, bundle_path="/System/Library/Frameworks/EventKit.framework")
EKEventStore = objc.lookUpClass("EKEventStore")
EKEvent = objc.lookUpClass("EKEvent")
NSDate = objc.lookUpClass("NSDate")

EVENT = 0  # EKEntityTypeEvent
STATUS = {0: "not_determined", 1: "restricted", 2: "denied", 3: "granted", 4: "write_only"}

_store = None
_lock = threading.Lock()


class AgendaError(Exception):
    pass


def status() -> str:
    return STATUS.get(EKEventStore.authorizationStatusForEntityType_(EVENT), "unknown")


def store():
    global _store
    with _lock:
        if _store is None:
            _store = EKEventStore.alloc().init()
        return _store


def request_access() -> None:
    """macOS prompt asking for full access to Calendar (first time only)."""
    global _store

    def done(granted, error):
        global _store
        _store = None  # a store created before access was granted sees nothing: recreate it

    store().requestFullAccessToEventsWithCompletion_(done)


def _nsdate(moment: dt.datetime):
    return NSDate.dateWithTimeIntervalSince1970_(moment.timestamp())


def _datetime(nsdate) -> dt.datetime:
    return dt.datetime.fromtimestamp(nsdate.timeIntervalSince1970())


def events(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Events between two instants, all calendars, sorted: title, start, end, all-day."""
    if status() != "granted":
        raise AgendaError("not_allowed")
    s = store()
    predicate = s.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), None)
    found = [{"title": str(e.title() or ""), "start": _datetime(e.startDate()), "end": _datetime(e.endDate()),
              "all_day": bool(e.isAllDay()), "calendar": str(e.calendar().title() or "")}
             for e in (s.eventsMatchingPredicate_(predicate) or [])]
    return sorted(found, key=lambda e: (not e["all_day"], e["start"]))


def add_event(title: str, start: dt.datetime, end: dt.datetime | None = None, all_day: bool = False) -> str:
    """Creates the event in the default calendar; returns the calendar's name."""
    if status() != "granted":
        raise AgendaError("not_allowed")
    s = store()
    calendar = s.defaultCalendarForNewEvents()
    if calendar is None:
        raise AgendaError("no_calendar")
    event = EKEvent.eventWithEventStore_(s)
    event.setTitle_(title)
    event.setAllDay_(all_day)
    event.setStartDate_(_nsdate(start))
    event.setEndDate_(_nsdate(end or start + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))))
    event.setCalendar_(calendar)
    result = s.saveEvent_span_error_(event, 0, None)  # (success, error) or success alone, depending on PyObjC
    ok, error = result if isinstance(result, tuple) else (result, None)
    if not ok:
        raise AgendaError(str(error.localizedDescription()) if error else "save")
    return str(calendar.title())
