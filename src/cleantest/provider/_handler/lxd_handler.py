#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for LXD-based test environments."""

import json
import multiprocessing
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from cleantest.pkg._base import Package
from cleantest.provider._handler.base_handler import Entrypoint, Handler, Result
from cleantest.provider.data.lxd_data import LXDConfig


@dataclass
class Instance:
    name: str
    image: str
    exists: bool = False


class LXDHandler(Handler):
    def _build(self, instance: Instance) -> None:
        if instance.exists is False:
            config = self._data.get_config(instance.image)
            config.name = instance.name
            self._client.instances.create(config.dict(), wait=True)
            instance = self._client.instances.get(instance.name)
            instance.start(wait=True)
            self._init(instance, config)
        else:
            if (tmp := self._client.instances.get(instance.name)).status.lower() == "stopped":
                tmp.start(wait=True)

    def _init(self, instance: Any, config: LXDConfig) -> None:
        if "ubuntu" in config.source.alias:
            cleantest_src = self._get_cleantest_source()
            injectable = self._construct_cleantest_injection("/root/cleantest_src.tar.gz")
            instance.files.put("/root/cleantest_src.tar.gz", cleantest_src)
            instance.files.put("/root/init_cleantest", injectable)
            instance.execute(["chmod", "+x", "/root/init_cleantest"])
            instance.execute(["/root/init_cleantest"])
            instance.execute(["apt", "update"])
            instance.execute(["apt", "install", "-y", "python3-pip"])
            instance.execute(["pip", "install", "pylxd", "pydantic"])
        else:
            NotImplementedError(f"{config.source.alias} injection not supported yet.")

    def _execute(self, test: str, instance: Instance) -> Any:
        instance = self._client.instances.get(instance.name)
        instance.files.put("/root/test", test)
        instance.execute(["chmod", "+x", "/root/test"])
        return instance.execute(["/root/test"], environment=self._env.dump())

    def _teardown(self, instance: Instance) -> None:
        instance = self._client.instance.get(instance.name)
        instance.stop(wait=True)
        instance.delete(wait=True)

    def _handle_start_env_hooks(self, instance: Instance) -> None:
        start_env_hooks = self._cleanconfig.get_start_env_hooks()
        while len(start_env_hooks) > 0:
            hook = start_env_hooks.pop()
            instance = self._client.instances.get(instance.name)
            if hook.packages is not None:
                for pkg in hook.packages:
                    self._handle_package_install(instance, pkg)

    def _handle_package_install(self, instance: Any, pkg: Package) -> None:
        dispatch = {"charmlib": lambda x: self._env.add(json.loads(x))}

        dump_data = pkg._dump()
        remote_file_path = f"/root/{os.path.basename(dump_data['path'])}"
        instance.files.put(remote_file_path, open(dump_data["path"], "rb").read())
        instance.files.put(
            "/root/install",
            self._construct_pkg_installer(pkg, remote_file_path, dump_data["hash"]),
        )
        instance.execute(["chmod", "+x", "/root/install"])
        result = instance.execute(["/root/install"])

        if (tmp := pkg.__class__.__name__.lower()) in dispatch:
            dispatch[tmp](result.stdout)

    def _process(self, result: Any) -> Result:
        return Result(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)

    def _check_exists(self, instance: Instance) -> Instance:
        if self._client.instances.exists(instance.name):
            instance.exists = True

        return instance

    def _construct_instance_metaclasses(self) -> List[Instance]:
        return [Instance(name=f"{self._name}-{i}", image=i) for i in self._image]


class Serial(Entrypoint, LXDHandler):
    def __init__(self, attr: Dict[str, Any], func: Callable) -> None:
        # TODO: Consider replacing the mount with explicit values.
        [setattr(self, k, v) for k, v in attr.items()]
        self.func = func

    def run(self) -> Dict[str, Result]:
        results = {}
        for i in self._construct_instance_metaclasses():
            self._build(self._check_exists(i))
            self._handle_start_env_hooks(i)
            result = self._execute(
                self._construct_testlet(self.func, [re.compile("@lxd(.*)")]), i
            )
            if self._preserve is False:
                self._teardown(i)
            results.update({i.name: self._process(result)})

        return results


class Parallel(Entrypoint, LXDHandler):
    def __init__(self, attr: Dict[str, Any], func: Callable) -> None:
        # TODO: Consider replacing the mount with explicit values.
        [setattr(self, k, v) for k, v in attr.items()]
        self.func = func

    def run(self) -> Dict[str, Result]:
        # TODO: Polish off multiprocessing for LXD tests.
        #   Data will need to be massaged so it can return in the proper format.
        # pool = multiprocessing.Pool(processes=4)
        # results = pool.map(self._target, self._construct_instance_metaclasses())
        ...

    def _target(self):
        # TODO: Test that methods defined in classes can serve as targets.
        ...


class LXDProvider:
    @staticmethod
    def serial(lxd, func: Callable) -> "Serial":
        return Serial(lxd.__dict__, func)

    @staticmethod
    def parallel(lxd, func: Callable) -> "Parallel":
        return Parallel(lxd.__dict__, func)
