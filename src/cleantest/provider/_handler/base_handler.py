#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Abstractions and utilities for test environment providers."""

from __future__ import annotations

import inspect
import os
import pathlib
import re
import tarfile
import tempfile
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

import cleantest


class HandlerError(Exception):
    ...


@dataclass
class Result:
    exit_code: int = None
    stdout: Any = None
    stderr: Any = None


class Entrypoint(ABC):
    """Abstract super-class for test environment provider entrypoints.

    Entrypoints define the tooling needed by cleantest to start a test.
    """

    @abstractmethod
    def run(self) -> Dict[str, Result]:
        ...


class Handler(ABC):
    """Abstract super-class for test environment handlers.

    Handlers define all the tooling needed to run tests inside the remote environment.
    """

    @abstractmethod
    def _init(self) -> None:
        ...

    @abstractmethod
    def _execute(self) -> Any:
        ...

    @abstractmethod
    def _process(self) -> Result:
        ...

    @abstractmethod
    def _handle_start_env_hooks(self) -> None:
        ...

    def _get_cleantest_source(self) -> bytes:
        for root, directory, file in os.walk(cleantest.__path__[0]):
            src_path = pathlib.Path(root)
            if src_path.name == cleantest.__name__:
                old_dir = os.getcwd()
                os.chdir(os.sep.join(str(src_path).split(os.sep)[:-1]))
                tar_path = pathlib.Path(tempfile.gettempdir()).joinpath(cleantest.__name__)
                with tarfile.open(tar_path, "w:gz") as tarball:
                    tarball.add(cleantest.__name__)
                os.chdir(old_dir)

                return tar_path.read_bytes()

        raise HandlerError(f"Could not find source directory for {cleantest.__name__}.")

    def _construct_cleantest_injection(self, path: str) -> str:
        return textwrap.dedent(
            f"""
            #!/usr/bin/env python3
            import site
            import tarfile
    
            site.getsitepackages()[0]
            tarball = tarfile.open("{path}", "r:gz")
            tarball.extractall(site.getsitepackages()[0])
            """.strip(
                "\n"
            )
        )

    def _construct_testlet(self, src: str, name: str, remove: List[re.Pattern] | None) -> str:
        """Construct Python source file to be run in subroutine.

        Args:
            src (str): TODO
            remove (List[re.Pattern]): TODO

        Returns:
            str: TODO

        Future:
            This will need more advanced logic if tests accept arguments.
        """
        try:
            if remove is not None:
                for pattern in remove:
                    src = re.sub(pattern, "", src)

            with tempfile.TemporaryFile(mode="w+t") as f:
                content = [
                    "#!/usr/bin/env python3\n",
                    f"{src}\n",
                    f"{name}()\n",
                ]
                f.writelines(content)
                f.seek(0)
                scriptlet = f.read()

            return scriptlet
        except OSError:
            raise HandlerError(f"Could not locate source code for testlet {name}.")

    def _construct_pkg_installer(self, pkg: object, file_path: str, hash: str) -> str:
        src = inspect.getsourcefile(pkg.__class__)
        if src is None:
            raise HandlerError(f"Could not get the source file of object {pkg.__class__.__name__}")

        with tempfile.TemporaryFile(mode="w+t") as f:
            content = [
                f"{open(src, 'rt').read()}\n",
                f"holder = {pkg.__class__.__name__}._load('{file_path}', '{hash}')\n",
                "holder._run()\n",
            ]
            f.writelines(content)
            f.seek(0)
            pkg_installer = f.read()

        return pkg_installer
