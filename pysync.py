import os
import pickle
import subprocess
import sys
import time
from shutil import copyfile

from watchdog.observers import Observer
from watchdog.events import LoggingEventHandler, FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent, \
    DirCreatedEvent, DirDeletedEvent, DirModifiedEvent, DirMovedEvent, FileSystemEventHandler
from watchdog.utils.dirsnapshot import DirectorySnapshot, DirectorySnapshotDiff
import logging


class MyEventHandler(FileSystemEventHandler):
    def __init__(self, src_dir, target_dir):
        self._src_dir = src_dir
        self._docker_container = ""
        if os.path.exists(target_dir):
            self._target_dir = target_dir
        else:
            self._docker_container = target_dir.split(":")[0]
            self._target_dir = target_dir.split(":")[1]

    # def dispatch(self, event):
    #     super().dispatch(event)

    # def on_any_event(self, event):
    #     super().on_any_event(event)

    def on_moved(self, event):
        try:
            if event.src_path != event.dest_path:
                src_path = event.src_path
                self.remove(src_path)
                dest_path = event.dest_path
                self.copy(dest_path)
        except AttributeError as e:
            pass

    def on_created(self, event):
        try:
            src_path = event.src_path
            self.copy(src_path)
        except AttributeError as e:
            pass

    def on_deleted(self, event):
        try:
            src_path = event.src_path
            self.remove(src_path)
        except AttributeError as e:
            pass

    def on_modified(self, event):
        try:
            src_path = event.src_path
            self.copy(src_path)
        except AttributeError as e:
            pass

    def copy(self, file_path):
        if not self.is_ignored(file_path):
            return
        print("copying " + file_path)
        file_path = os.path.abspath(file_path)
        target_path = os.path.relpath(file_path, self._src_dir)
        target_path = os.path.join(self._target_dir, target_path)
        if self._docker_container == "":
            target_dir, target_file = os.path.split(target_path)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
                    copyfile(file_path, target_path)
                except (PermissionError, FileNotFoundError) as e:
                    print(e)
        else:
            target_path = os.path.normpath(target_path)
            target_path = target_path.replace("\\", "/")
            os.system("docker cp {src_path} {container}:{target_path}".format_map({
                "src_path": file_path,
                "container": self._docker_container,
                "target_path": target_path
            }))

    def remove(self, file_path):
        if not self.is_ignored(file_path):
            return
        print("removing " + file_path)
        file_path = os.path.abspath(file_path)
        target_path = os.path.relpath(file_path, self._src_dir)
        target_path = os.path.join(self._target_dir, target_path)
        if self._docker_container == "":
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except PermissionError as e:
                    print(e)
        else:
            target_path = os.path.normpath(target_path)
            target_path = target_path.replace("\\", "/")
            os.system("docker exec -it {container} rm {file_path}".format_map({
                "container": self._docker_container,
                "file_path": target_path
            }))

    def is_ignored(self, file_path):
        file_path = os.path.normpath(file_path)
        os.chdir(self._src_dir)
        list_of_files = subprocess.check_output("git ls-files", shell=True).splitlines()
        for file in list_of_files:
            file = file.decode("unicode_escape")
            normfile = os.path.normpath(os.path.join(self._src_dir, file))
            if normfile == file_path:
                return True
        return False


class _EmptySnapshot(DirectorySnapshot):
    @property
    def stat_snapshot(self):
        return dict()

    @property
    def paths(self):
        return set()


class PersistantObserver(Observer):
    def __init__(self, *args, **kwargs):
        """
        Check if watching folders has changed since last observation.
        If change detected, emit corresponding events at suscribers handlers.
        At the `Observer.stop`, save states of folders with pickle for the next observation.
        PARAMETERS
        ==========
        save_to : unicode
            path where save pickle dumping
        protocol (optionnal): int
            protocol used for dump current states of watching folders
        """
        self._filename = kwargs.pop('save_to')
        self._protocol = kwargs.pop('protocol', 0)
        Observer.__init__(self, *args, **kwargs)

    def start(self, *args, **kwargs):
        previous_snapshots = dict()
        if os.path.exists(self._filename):
            with open(self._filename, 'rb') as f:
                previous_snapshots = pickle.load(f)

        for watcher, handlers in self._handlers.items():
            try:
                path = watcher.path
                curr_snap = DirectorySnapshot(path)
                pre_snap = previous_snapshots.get(path, _EmptySnapshot(path))
                diff = DirectorySnapshotDiff(pre_snap, curr_snap)
                for handler in handlers:
                    # Dispatch files modifications
                    for new_path in diff.files_created:
                        handler.dispatch(FileCreatedEvent(new_path))
                    for del_path in diff.files_deleted:
                        handler.dispatch(FileDeletedEvent(del_path))
                    for mod_path in diff.files_modified:
                        handler.dispatch(FileModifiedEvent(mod_path))
                    for src_path, mov_path in diff.files_moved:
                        handler.dispatch(FileMovedEvent(src_path, mov_path))

                    # Dispatch directories modifications
                    for new_dir in diff.dirs_created:
                        handler.dispatch(DirCreatedEvent(new_dir))
                    for del_dir in diff.dirs_deleted:
                        handler.dispatch(DirDeletedEvent(del_dir))
                    for mod_dir in diff.dirs_modified:
                        handler.dispatch(DirModifiedEvent(mod_dir))
                    for src_path, mov_path in diff.dirs_moved:
                        handler.dispatch(DirMovedEvent(src_path, mov_path))
            except PermissionError as e:
                print(e)

        Observer.start(self, *args, **kwargs)

    def stop(self, *args, **kwargs):
        try:
            snapshots = {handler.path: DirectorySnapshot(handler.path) for handler in self._handlers.keys()}
            with open(self._filename, 'wb') as f:
                pickle.dump(snapshots, f, self._protocol)
            Observer.stop(self, *args, **kwargs)
        except PermissionError as e:
            print(e)


def observe(src_path, event_handler, persistent=True):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
    if persistent:
        observer = PersistantObserver(save_to='C:\\temp\\test.pickle', protocol=-1)
    else:
        observer = Observer()
    observer.schedule(event_handler, src_path, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def compare_dirs(src_path, dest_path):
    src_snap = DirectorySnapshot(src_path)
    dest_path = DirectorySnapshot(dest_path)
    diff = DirectorySnapshotDiff(src_snap, dest_path)
    print(diff.files_modified)


def sync(src_dir, target_dir):
    # event_handler = LoggingEventHandler()
    event_handler = MyEventHandler(src_dir, target_dir)
    observe(src_dir, event_handler)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else '.'
    sync(path, "D:\share")
    # observe_over_sessions(path)
    # compare_dirs("C:\\New folder\\temp", "C:\\temp")
    pass
