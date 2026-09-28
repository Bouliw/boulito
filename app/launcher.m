// Boulito.app: minimal launcher for the assistant.
//
// It remains the parent process of Python: macOS then grants the permissions (microphone,
// Input Monitoring, Safari automation) to Boulito.app, not to Terminal or Python.
// Source version: it runs the ./voix script in the project folder (same settings as from Terminal).
// .dmg version: the app contains its own Python (Contents/Resources/python) and the code (Resources/boulito); the
// data (settings, models, logs) goes to ~/Library/Application Support/Boulito, and the app is never modified.
// Arguments received: "start" by default; output written to logs/YYYY-MM-DD.app.log (data folder).
//
// Notifications: Python is not an app, so its notifications would go through osascript (with the icon of
// Script Editor). So it writes "title<TAB>text" on descriptor 3 (BOULITO_NOTIFY_FD), and this
// launcher, which is the Boulito app, posts them itself: Boulito's name and icon, configurable in
// System Settings → Notifications. Permission is only requested on the first notification.

#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <UserNotifications/UserNotifications.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>

extern char **environ;
static pid_t child = 0;
static dispatch_group_t pending;  // notifications not yet handed over to macOS: waited for before quitting

static void forward(int sig) {
    if (child > 0) kill(child, sig);  // Quitting Boulito also stops the assistant
}

@interface Presenter : NSObject <UNUserNotificationCenterDelegate>
@end

@implementation Presenter
- (void)userNotificationCenter:(UNUserNotificationCenter *)center willPresentNotification:(UNNotification *)notification
         withCompletionHandler:(void (^)(UNNotificationPresentationOptions))completionHandler {
    completionHandler(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList);
}
@end

static Presenter *presenter;

// Quitting the app (Activity Monitor, logging out, "quit app"): the assistant is stopped cleanly;
// the app exits once it has finished (volume restored, download stopped), not before
@interface Quitter : NSObject <NSApplicationDelegate>
@end

@implementation Quitter
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (child <= 0) return NSTerminateNow;
    kill(child, SIGTERM);  // the assistant shuts down cleanly; the thread waiting for it then terminates the app
    return NSTerminateLater;  // never "Cancel": macOS would interrupt the logout or the shutdown
}
@end

static Quitter *quitter;
static char status_file[PATH_MAX];  // authorization status, read by the Setup window (Python)

// Writes "authorized", "denied" or "not_determined" to .cache/notifications (file replaced in one go)
static void save_status(UNUserNotificationCenter *center) {
    [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) {
        const char *state = settings.authorizationStatus == UNAuthorizationStatusNotDetermined ? "not_determined"
                          : settings.authorizationStatus == UNAuthorizationStatusDenied ? "denied" : "authorized";
        char tmp[PATH_MAX];
        snprintf(tmp, sizeof tmp, "%s.tmp", status_file);
        FILE *f = fopen(tmp, "w");
        if (!f) return;
        fputs(state, f);
        fclose(f);
        rename(tmp, status_file);
    }];
}

// Assistant started (not a one-off command): asks for permission from the first launch, then
// checks the status every 3 s so that the Setup window shows it live
static void watch_authorization(void) {
    UNUserNotificationCenter *center = [UNUserNotificationCenter currentNotificationCenter];
    [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) {
        if (settings.authorizationStatus == UNAuthorizationStatusNotDetermined)
            [center requestAuthorizationWithOptions:UNAuthorizationOptionAlert
                                  completionHandler:^(BOOL granted, NSError *error) { save_status(center); }];
        save_status(center);
    }];
    dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
    dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), 3 * NSEC_PER_SEC, NSEC_PER_SEC);
    dispatch_source_set_event_handler(timer, ^{ save_status(center); });
    dispatch_resume(timer);
    static dispatch_source_t keep;  // kept alive as long as the app runs
    keep = timer;
    (void)keep;
}

static void post(UNUserNotificationCenter *center, NSString *title, NSString *body) {
    UNMutableNotificationContent *content = [[UNMutableNotificationContent alloc] init];
    content.title = title;
    content.body = body;
    UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:[[NSUUID UUID] UUIDString]
                                                                          content:content trigger:nil];
    dispatch_group_enter(pending);
    [center addNotificationRequest:request withCompletionHandler:^(NSError *error) {
        if (error) fprintf(stderr, "· notifications: refused by macOS: %s\n", error.localizedDescription.UTF8String);
        dispatch_group_leave(pending);
    }];
}

// Reads the "title<TAB>text" lines sent by Python and posts them (separate thread: the main thread waits for Python)
static void notifications(int fd) {
    dispatch_group_enter(pending);  // until the end of the channel (Python finished) and the last notification read
    [NSThread detachNewThreadWithBlock:^{
        FILE *in = fdopen(fd, "r");
        if (!in) return;
        UNUserNotificationCenter *center = [UNUserNotificationCenter currentNotificationCenter];
        presenter = [[Presenter alloc] init];
        center.delegate = presenter;
        __block BOOL asked = NO;
        char *line = NULL;
        size_t size = 0;
        while (getline(&line, &size, in) > 0) {
            @autoreleasepool {
                NSString *text = [[NSString stringWithUTF8String:line]
                    stringByTrimmingCharactersInSet:[NSCharacterSet newlineCharacterSet]];
                if (!text.length) continue;
                NSRange tab = [text rangeOfString:@"\t"];
                NSString *title = tab.location == NSNotFound ? @"Boulito" : [text substringToIndex:tab.location];
                NSString *body = tab.location == NSNotFound ? text : [text substringFromIndex:tab.location + 1];
                if (!asked) {  // first notification: macOS asks for permission once
                    asked = YES;
                    dispatch_semaphore_t done = dispatch_semaphore_create(0);
                    [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert)
                                          completionHandler:^(BOOL granted, NSError *error) {
                        if (!granted)
                            fprintf(stderr, "· notifications: not allowed for Boulito (System Settings → Notifications)%s%s\n",
                                    error ? " — " : "", error ? error.localizedDescription.UTF8String : "");
                        dispatch_semaphore_signal(done);
                    }];
                    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC));
                }
                post(center, title, body);
            }
        }
        free(line);
        fclose(in);
        dispatch_group_leave(pending);
    }];
}

static void parent_dir(char *path) {
    char *slash = strrchr(path, '/');
    if (slash) *slash = '\0';
}

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], bundle[PATH_MAX], project[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, bundle)) return 1;
    for (int i = 0; i < 3; i++) parent_dir(bundle);  // Boulito.app/Contents/MacOS/Boulito → Boulito.app
    snprintf(project, sizeof project, "%s", bundle);
    parent_dir(project);  // source version: the app is in the project folder

    char script[PATH_MAX], python[PATH_MAX], data[PATH_MAX];
    snprintf(script, sizeof script, "%s/voix", project);
    snprintf(python, sizeof python, "%s/Contents/Resources/python/bin/python3", bundle);
    int packaged = access(python, X_OK) == 0;  // .dmg version: Python shipped with the app
    if (packaged) {
        // No PYTHON… variable from outside: only the app's code runs, with its permissions
        for (int again = 1; again;) {
            again = 0;
            for (char **env = environ; *env; env++) {
                if (strncmp(*env, "PYTHON", 6) == 0) {
                    char name[256];
                    size_t length = strcspn(*env, "=");
                    if (length >= sizeof name) length = sizeof name - 1;
                    memcpy(name, *env, length);
                    name[length] = '\0';
                    unsetenv(name);
                    again = 1;
                    break;
                }
            }
        }
        const char *custom = getenv("BOULITO_DATA");
        if (custom && *custom)
            snprintf(data, sizeof data, "%s", custom);
        else
            snprintf(data, sizeof data, "%s/Library/Application Support/Boulito", NSHomeDirectory().UTF8String);
        [[NSFileManager defaultManager] createDirectoryAtPath:@(data) withIntermediateDirectories:YES attributes:nil error:nil];
        setenv("BOULITO_DATA", data, 1);
        char code[PATH_MAX];
        snprintf(code, sizeof code, "%s/Contents/Resources/boulito/src", bundle);
        setenv("PYTHONPATH", code, 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  // never a file written into the app: its signature checks that
        setenv("PYTHONNOUSERSITE", "1", 1);
    } else {
        snprintf(data, sizeof data, "%s", project);
    }
    setenv("BOULITO_APP", bundle, 1);  // open at login and restart: this very app

    char logs[PATH_MAX], log[PATH_MAX], day[16];
    time_t now = time(NULL);
    strftime(day, sizeof day, "%Y-%m-%d", localtime(&now));
    snprintf(logs, sizeof logs, "%s/logs", data);
    snprintf(log, sizeof log, "%s/%s.app.log", logs, day);
    mkdir(logs, 0700);
    chmod(logs, 0700);  // the logs quote what was said: readable by the user only (even if created by an older version)
    char cache[PATH_MAX];
    snprintf(cache, sizeof cache, "%s/.cache", data);
    mkdir(cache, 0755);
    snprintf(status_file, sizeof status_file, "%s/notifications", cache);

    pending = dispatch_group_create();
    int logfd = open(log, O_WRONLY | O_CREAT | O_APPEND, 0600);  // the launcher's messages go to the log too
    if (logfd >= 0) {
        fchmod(logfd, 0600);
        dup2(logfd, STDERR_FILENO);
        close(logfd);
        setvbuf(stderr, NULL, _IONBF, 0);
    }
    int pipefd[2] = {-1, -1};  // notification channel: Python writes on its descriptor 3
    int notify = pipe(pipefd) == 0;
    if (notify) fcntl(pipefd[0], F_SETFD, FD_CLOEXEC);

    posix_spawn_file_actions_t files;
    posix_spawn_file_actions_init(&files);
    posix_spawn_file_actions_addopen(&files, 1, log, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&files, 1, 2);
    if (notify) {
        posix_spawn_file_actions_adddup2(&files, pipefd[1], 3);
        setenv("BOULITO_NOTIFY_FD", "3", 1);
    }
    char pid[16];
    snprintf(pid, sizeof pid, "%d", getpid());
    setenv("BOULITO_APP_PID", pid, 1);  // to restart the app only once this one has exited

    // Only startup and diagnostics: another program cannot use the permissions of
    // Boulito (Accessibility, microphone, Automation) to type, listen or control Safari ("--args run …")
    if (argc > 1 && strcmp(argv[1], "start") && strcmp(argv[1], "diag") && strcmp(argv[1], "check")) {
        fprintf(stderr, "· Boulito.app only runs: start, diag, check (refused: %s)\n", argv[1]);
        return 2;
    }
    char *args[argc + 4];
    int n = 0;
    if (packaged) {  // the app's Python: python3 -m voix …
        args[n++] = python;
        args[n++] = "-m";
        args[n++] = "voix";
    } else {
        args[n++] = script;
    }
    if (argc > 1)
        for (int i = 1; i < argc; i++) args[n++] = argv[i];
    else
        args[n++] = "start";
    args[n] = NULL;

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    if (posix_spawn(&child, args[0], &files, NULL, args, environ) != 0) return 1;
    if (notify) {
        close(pipefd[1]);  // only Python keeps the write end: the channel ends when it exits
        notifications(pipefd[0]);
    }
    // The main thread keeps an event loop (macOS's notification center needs one);
    // the end of Python is awaited on another thread
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        int status = 0;
        while (waitpid(child, &status, 0) < 0) {
        }
        // The last notifications (and the permission request, the first time) have up to 30 s to go out
        dispatch_group_wait(pending, dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC));
        dispatch_sync(dispatch_get_main_queue(), ^{ [NSApp replyToApplicationShouldTerminate:YES]; });
        exit(WIFEXITED(status) ? WEXITSTATUS(status) : 1);
    });
    // A real app for macOS (no Dock icon, no window): otherwise the notification center refuses to register it
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    NSApp.delegate = quitter = [[Quitter alloc] init];
    if (argc == 1 || strcmp(argv[1], "start") == 0) {
        UNUserNotificationCenter.currentNotificationCenter.delegate = presenter = [[Presenter alloc] init];
        watch_authorization();
    }
    [NSApp run];
    return 0;
}
