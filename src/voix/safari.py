"""Safari control through AppleScript: open YouTube and run JavaScript in its tab.

Security:
- JavaScript is only sent to a tab whose address starts with https://www.youtube.com/
  (checked in this module's AppleScript), and the script itself refuses to run anywhere else;
- running JavaScript never launches Safari: only open_url() opens it, on request.

Permissions: Automation → Safari for the app that runs Voix (Terminal during
development), and in Safari the "Allow JavaScript from Apple Events" setting.
"""

import sys
import threading

from .i18n import t

YOUTUBE = "https://www.youtube.com/"

# YouTube tab: the active tab of the front window if it is on YouTube, otherwise the first one found
FIND_TAB = f"""
    set target to missing value
    set targetWindow to missing value
    try
        set t to current tab of front window
        if (URL of t as text) starts with "{YOUTUBE}" then
            set target to t
            set targetWindow to front window
        end if
    end try
    if target is missing value then
        repeat with w in windows
            repeat with t in tabs of w
                try
                    if (URL of t as text) starts with "{YOUTUBE}" then
                        set target to contents of t
                        set targetWindow to contents of w
                        exit repeat
                    end if
                end try
            end repeat
            if target is not missing value then exit repeat
        end repeat
    end if"""


class SafariError(Exception):
    pass


class NoYouTubeTab(SafariError):
    pass


def quote(s: str) -> str:
    """AppleScript string in quotes."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def on_main_thread(function, timeout: float = 30):
    """Runs function on the main thread (which runs the event loop) and waits."""
    from PyObjCTools import AppHelper

    done, box = threading.Event(), {}

    def run():
        try:
            box["value"] = function()
        except BaseException as e:
            box["error"] = e
        finally:
            done.set()

    AppHelper.callAfter(run)
    if not done.wait(timeout):
        raise SafariError(t("safari.not_responding"))
    if "error" in box:
        raise box["error"]
    return box["value"]


def applescript(source: str) -> str:
    """Runs an AppleScript in-process (NSAppleScript, 10 times faster than osascript).

    NSAppleScript can only be used from the main thread: other threads hand over to it.
    """
    if threading.current_thread() is not threading.main_thread():
        return on_main_thread(lambda: applescript(source))
    from Foundation import NSAppleScript

    result, error = NSAppleScript.alloc().initWithSource_(source).executeAndReturnError_(None)
    if error is not None:
        number = error.get("NSAppleScriptErrorNumber")
        message = str(error.get("NSAppleScriptErrorMessage") or "")
        if number == -1743:
            raise SafariError(t("safari.automation"))
        if number == 1001:
            raise NoYouTubeTab(t("safari.closed"))
        if number == 1002:
            raise NoYouTubeTab(t("safari.no_tab"))
        if "JavaScript" in message and "Apple" in message:
            raise SafariError(t("safari.javascript"))
        # Raw AppleScript message: only in the log (stderr → logs/…app.log), never shown to the user
        print(f"· Safari: AppleScript error {number}: {message}", file=sys.stderr, flush=True)
        raise SafariError(t("safari.failed", number=number if number is not None else "?"))
    return (result.stringValue() or "") if result is not None else ""


def run_js(js: str) -> str:
    """Runs JavaScript in the YouTube tab and returns its result (text)."""
    return applescript(f"""
if application "Safari" is not running then error "Safari not running" number 1001
tell application "Safari"
{FIND_TAB}
    if target is missing value then error "No YouTube tab" number 1002
    return do JavaScript {quote(js)} in target
end tell""")


def has_youtube_tab() -> bool:
    return applescript(f"""
if application "Safari" is not running then return "no"
tell application "Safari"
{FIND_TAB}
    if target is missing value then return "no"
    return "yes"
end tell""") == "yes"


def show_youtube() -> None:
    """Brings Safari to the front, on the YouTube tab."""
    applescript(f"""
tell application "Safari"
    activate
{FIND_TAB}
    if target is missing value then error "No YouTube tab" number 1002
    set current tab of targetWindow to target
    set index of targetWindow to 1
end tell""")


def open_url(url: str) -> None:
    """Opens a YouTube address in the existing YouTube tab, otherwise in a new tab.

    Launches Safari if needed and brings it to the front.
    """
    if not url.startswith(YOUTUBE):
        raise SafariError(t("safari.not_youtube", url=url))
    applescript(f"""
tell application "Safari"
    activate
{FIND_TAB}
    if target is not missing value then
        set current tab of targetWindow to target
        set index of targetWindow to 1
        set URL of target to {quote(url)}
    else if (count of windows) is 0 then
        make new document with properties {{URL:{quote(url)}}}
    else
        tell front window to set current tab to (make new tab with properties {{URL:{quote(url)}}})
    end if
end tell""")
