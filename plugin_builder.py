from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
from collections.abc import Callable

import click
from watchdog.events import FileSystemEvent, RegexMatchingEventHandler
from watchdog.observers import Observer

# get the directory of the current file
# this is used to watch for changes in this directory
current_dir = os.path.dirname(os.path.abspath(__file__))

# these are the patterns to watch for changes in this directory
# if in editable mode, the builder will rerun when these files change
REBUILD_REGEXES = [
    r".*\.py$",
    r".*\.js$",
    r".*\.ts$",
    r".*\.tsx$",
    r".*\.scss$",
]

# ignore these patterns in particular
# prevents infinite loops when the builder is rerun
IGNORE_REGEXES = [
    r".*/dist/.*",
    r".*/build/.*",
    r".*/node_modules/.*",
    r".*/_js/.*",
    # ignore hidden files and directories
    r".*/\..*/.*",
]

# the path where the python files are located relative to this script
# modify this if the python files are moved
PYTHON_DIR = "."
# the path where the JS files are located relative to this script
# modify this if the JS files are moved
JS_DIR = "./src/js"


class PluginsChangedHandler(RegexMatchingEventHandler):
    """
    A handler that watches for changes and reruns the function when changes are detected

    Args:
        func: The function to run when changes are detected
        stop_event: The event to signal the function to stop

    Attributes:
        func: The function to run when changes are detected
        stop_event: The event to signal the function to stop
        rerun_lock: A lock to prevent multiple reruns from occurring at the same time
    """

    def __init__(self, func: Callable, stop_event: threading.Event) -> None:
        super().__init__(regexes=REBUILD_REGEXES, ignore_regexes=IGNORE_REGEXES)

        self.func = func

        # A flag to indicate whether the function should continue running
        # Also prevents unnecessary reruns
        self.stop_event = stop_event

        # A lock to prevent multiple reruns from occurring at the same time
        self.rerun_lock = threading.Lock()

        # always have an initial run
        threading.Thread(target=self.attempt_rerun).start()

    def attempt_rerun(self) -> None:
        """
        Attempt to rerun the function.
        If the stop event is set, do not rerun because a rerun has already been scheduled.
        """
        self.stop_event.set()
        with self.rerun_lock:
            self.stop_event.clear()
            self.func()

    def event_handler(self, event: FileSystemEvent) -> None:
        """
        Handle any file system event

        Args:
            event: The event that occurred
        """
        if self.stop_event.is_set():
            # a rerun has already been scheduled on another thread
            print(
                f"File {event.src_path} {event.event_type}, rerun has already been scheduled"
            )
            return
        print(f"File {event.src_path} {event.event_type}, new rerun scheduled")
        threading.Thread(target=self.attempt_rerun).start()

    def on_created(self, event: FileSystemEvent) -> None:
        """
        Handle a file creation event

        Args:
            event: The event that occurred
        """
        self.event_handler(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """
        Handle a file deletion event

        Args:
            event: The event that occurred
        """
        self.event_handler(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        """
        Handle a file modification event

        Args:
            event: The event that occurred
        """
        self.event_handler(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        """
        Handle a file move event

        Args:
            event: The event that occurred

        Returns:

        """
        self.event_handler(event)


def clean_build_dist() -> None:
    """
    Remove the build and dist directories.
    """
    # these folders may not exist, so ignore the errors
    if os.path.exists(f"{PYTHON_DIR}/build"):
        os.system(f"rm -rf {PYTHON_DIR}/build")
    if os.path.exists(f"{PYTHON_DIR}/dist"):
        os.system(f"rm -rf {PYTHON_DIR}/dist")


def run_command(command: str) -> None:
    """
    Run a command and exit if it fails.
    This should only be used in a non-main thread.

    Args:
        command: The command to run.

    Returns:
        None
    """
    code = os.system(command)
    if code != 0:
        os._exit(1)


def run_build() -> None:
    """
    Build the plugin
    """

    clean_build_dist()

    click.echo("Building plugin")
    run_command(f"uv build --wheel {PYTHON_DIR}")


def run_install(
    reinstall: bool,
) -> None:
    """
    Install plugins that have been built

    Args:
        reinstall: Whether to reinstall the plugins.
            If True, the --reinstall and --no-deps flags are added to uv pip install.

    Returns:
        None
    """
    install = "uv pip install"
    if reinstall:
        install += " --reinstall --no-deps"

    click.echo("Installing plugin")
    run_command(f"{install} {PYTHON_DIR}/dist/*.whl")


def run_build_js() -> None:
    """
    Build the JS files for the plugin.

    Installs the JS dependencies first if they are missing, so the plugin can be
    built entirely from the repo root (via `uv run plugin_builder.py`) without ever
    needing to `cd src/js` by hand.
    """
    if not os.path.exists(f"{JS_DIR}/node_modules"):
        click.echo("Installing JS dependencies")
        run_command(f"npm --prefix {JS_DIR} install")
    click.echo("Building the JS plugin")
    run_command(f"npm run build --prefix {JS_DIR}")


def copy_examples_to_notebooks(data_dir: str) -> None:
    """
    Sync every ``examples/*.py`` file into ``<data_dir>/storage/notebooks/``.

    Design decision — **copy, not symlink**:
    Symlinks from inside the Deephaven notebook store back into the repo tree
    may not resolve correctly depending on how the JVM process resolves paths,
    whether the container mounts differ, or whether the IDE file-browser follows
    symlinks.  A plain file copy is always robust.  The copy is unconditional
    (overwrite on every server start) so edits to the source files propagate
    automatically the next time the server is launched.

    This is a true sync, not just a copy: any ``*.py`` in the notebooks dir
    with no counterpart in ``examples/`` is deleted, so renamed or removed
    demos don't linger in the Web IDE with a stale API.

    Args:
        data_dir: Absolute path to the Deephaven data directory.
    """
    notebooks_dir = os.path.join(data_dir, "storage", "notebooks")
    os.makedirs(notebooks_dir, exist_ok=True)

    examples_dir = os.path.join(current_dir, "examples")
    example_names = {
        os.path.basename(src) for src in glob.glob(os.path.join(examples_dir, "*.py"))
    }

    for stale in glob.glob(os.path.join(notebooks_dir, "*.py")):
        if os.path.basename(stale) not in example_names:
            os.remove(stale)
            click.echo(f"Removed stale notebook {os.path.basename(stale)}")

    for name in sorted(example_names):
        dst = os.path.join(notebooks_dir, name)
        shutil.copy2(os.path.join(examples_dir, name), dst)
        click.echo(f"Copied example {name} → {dst}")


def build_server_args(
    server_arg: tuple[str],
    data_dir: str | None = None,
    psk: str | None = None,
) -> list[str]:
    """
    Build the server arguments to pass to the deephaven server.

    By default, the --no-browser flag is added unless the user supplies --browser/--no-browser.
    If data_dir is provided, the directory is mounted as the Deephaven data directory via the
    `-Ddeephaven.dataDir=...` JVM system property (there is no dedicated CLI flag for this). The
    Web IDE surfaces notebooks from `<data_dir>/storage/notebooks`, so that subfolder is created
    if missing and any example scripts placed there appear in the IDE's file explorer.

    If psk is provided, the server's pre-shared key is pinned to it via the
    `-Dauthentication.psk=...` JVM property (PSK is the pip server's default auth handler, so the
    property alone overrides the otherwise-random key — log in with this value).

    Both the data dir and the PSK are JVM system properties, so they are combined into a SINGLE
    `--jvm-args` value (the flag takes one space-separated string). They are skipped entirely if
    the user passed their own `--jvm-args`, to avoid clobbering it.

    Args:
        server_arg: Extra arguments to pass through to the server.
        data_dir: Directory to mount as the Deephaven data directory, or None to leave the default.
        psk: Pre-shared key to pin for server auth, or None to leave the default (random) key.
    """
    server_args: list[str] = []

    # Assemble JVM system properties, unless the user supplied their own --jvm-args.
    if not any("--jvm-args" in arg for arg in server_arg):
        jvm_props: list[str] = []
        if data_dir:
            abs_dir = os.path.abspath(data_dir)
            # Ensure the notebooks dir exists, then populate it with example scripts.
            os.makedirs(os.path.join(abs_dir, "storage", "notebooks"), exist_ok=True)
            copy_examples_to_notebooks(abs_dir)
            jvm_props.append(f"-Ddeephaven.dataDir={abs_dir}")
        if psk:
            jvm_props.append(f"-Dauthentication.psk={psk}")
        if jvm_props:
            server_args += ["--jvm-args", " ".join(jvm_props)]

    # Default to --no-browser unless the user expressed a browser preference.
    if not any(arg in ("--browser", "--no-browser") for arg in server_arg):
        server_args.append("--no-browser")

    server_args += list(server_arg)
    return server_args


def handle_args(
    build: bool,
    install: bool,
    reinstall: bool,
    server: bool,
    server_arg: tuple[str],
    js: bool,
    data_dir: str | None,
    psk: str | None,
    stop_event: threading.Event,
) -> None:
    """
    Handle all arguments for the builder command

    Args:
        build: True to build the plugins
        install: True to install the plugins
        reinstall: True to reinstall the plugins
        server: True to run the deephaven server after building and installing the plugins
        server_arg: The arguments to pass to the server
        js: True to build the JS files for the plugins
        data_dir: Directory to mount as the Deephaven data directory when running the server
        psk: Pre-shared key to pin for server auth, or None for the default random key
        stop_event: The event to signal the function to stop
    """
    # it is possible that the stop event is set before this function is called
    if stop_event.is_set():
        return

    # default is to install, but don't if just configuring
    if not any([build, install, reinstall, js]):
        js = True
        install = True

    # if this thread is signaled to stop, return after the current command
    # instead of in the middle of a command, which could leave the environment in a bad state
    if stop_event.is_set():
        return

    if js:
        run_build_js()

    if stop_event.is_set():
        return

    if build or install or reinstall:
        run_build()

    if stop_event.is_set():
        return

    if install or reinstall:
        run_install(reinstall)

    if stop_event.is_set():
        return

    if server or server_arg:
        server_args = build_server_args(server_arg, data_dir, psk)

        click.echo(f"Running deephaven server with args: {server_args}")
        process = subprocess.Popen(["deephaven", "server"] + server_args)

        # waiting on either the process to finish or the stop event to be set
        while not stop_event.wait(1):
            poll = process.poll()
            if poll is not None:
                # process threw an error or was killed, so exit
                os._exit(process.returncode)

        # stop event is set, so kill the process
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@click.command(
    short_help="Build and install plugin.",
    help="Build and install plugins. By default, all plugins with the necessary file are used unless specified via the plugins arg.",
)
@click.option("--build", "-b", is_flag=True, help="Build the plugin.")
@click.option(
    "--install",
    "-i",
    is_flag=True,
    help="Install the plugin. This is the default behavior if no flags are provided.",
)
@click.option(
    "--reinstall",
    "-r",
    is_flag=True,
    help="Reinstall the plugin. This adds the --reinstall and --no-deps flags to uv pip install. Useful if the plugin has already been installed and does not have a new version number.",
)
@click.option(
    "--server",
    "-s",
    is_flag=True,
    help="Run the deephaven server after building and installing the plugin. The repo root is mounted as the Deephaven data directory unless --data-dir is given.",
)
@click.option(
    "--server-arg",
    "-sa",
    default=tuple(),
    multiple=True,
    help="Run the deephaven server after building and installing the plugin with the provided argument.",
)
@click.option(
    "--data-dir",
    "-d",
    default=None,
    type=click.Path(file_okay=False),
    help="Directory to mount as the Deephaven data directory when running the server. Example scripts placed in <data-dir>/storage/notebooks appear in the Web IDE. Defaults to the repo root when running the server.",
)
@click.option(
    "--dev",
    is_flag=True,
    help="Convenience flag for the full dev loop: equivalent to --reinstall --js --server. Builds the JS, (re)installs the plugin, and starts a server with the repo mounted as the data directory. Also pins the server PSK to 'iris' (log in with that key).",
)
@click.option(
    "--js",
    "-j",
    is_flag=True,
    help="Build the JS files for the plugin.",
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Run the other provided commands in an editable-like mode, watching for changes. This will rerun all other commands (except configure) when files are changed. The top level directory of this project is watched.",
)
def builder(
    build: bool,
    install: bool,
    reinstall: bool,
    server: bool,
    server_arg: tuple[str],
    data_dir: str | None,
    dev: bool,
    js: bool,
    watch: bool,
) -> None:
    """
    Build and install plugins.

    Args:
        build: True to build the plugin
        install: True to install the plugin
        reinstall: True to reinstall the plugin
        server: True to run the deephaven server after building and installing the plugin
        server_arg: The arguments to pass to the server
        data_dir: Directory to mount as the Deephaven data directory when running the server
        dev: Convenience flag equivalent to --reinstall --js --server
        js: True to build the JS files for the plugin
        watch: True to rerun the other commands when files are changed
    """
    # --dev is sugar for the full dev loop.
    psk: str | None = None
    if dev:
        reinstall = True
        js = True
        server = True
        # Pin a predictable PSK so the dev server login is always the same.
        psk = "iris"

    # When running the server, default the mounted data directory to the repo root so example
    # scripts in <repo>/storage/notebooks show up in the Web IDE.
    if (server or server_arg) and data_dir is None:
        data_dir = current_dir

    stop_event = threading.Event()

    def run_handle_args() -> None:
        """
        Run the handle_args function with the provided arguments
        """
        handle_args(
            build,
            install,
            reinstall,
            server,
            server_arg,
            js,
            data_dir,
            psk,
            stop_event,
        )

    if not watch:
        # since editable is not specified, only run the handler once
        # call it from a thread to allow the usage of os._exit to exit the process
        # rather than sys.exit because sys.exit will not exit the process when called from a thread
        # and os._exit should be called from a thread
        thread = threading.Thread(target=run_handle_args)
        thread.start()
        thread.join()
        return

    # editable is specified, so run the handler in a loop that watches for changes and
    # reruns the handler when changes are detected
    event_handler = PluginsChangedHandler(run_handle_args, stop_event)
    observer = Observer()
    observer.schedule(event_handler, current_dir, recursive=True)
    observer.start()
    try:
        while True:
            input()
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    builder()
