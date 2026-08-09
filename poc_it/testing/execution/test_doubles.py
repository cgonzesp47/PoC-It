from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Literal, Optional

MAX_DEPENDENCY_CHAIN_DEPTH = 4


@dataclass(frozen=True)
class MethodProtocol:
    method_name: str
    is_async: bool = False
    returns_protocol: Optional[str] = None

    @property
    def name(self) -> str:
        """Alias temporal de compatibilidad."""
        return self.method_name


@dataclass(frozen=True)
class DependencyProtocol:
    dependency_fqn: str
    methods: list[MethodProtocol]
    strict: bool = True
    protocol_id: str = "root"
    nested_protocols: dict[str, "DependencyProtocol"] | None = None

    def nested(self) -> dict[str, "DependencyProtocol"]:
        return dict(self.nested_protocols or {})


@dataclass(frozen=True)
class DependencyBehavior:
    dependency_fqn: str
    action: Literal["return", "raise", "yield", "async_return"]
    value: Any = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    method_name: str = ""
    call_path: tuple[str, ...] = ()

    def normalized_call_path(self) -> tuple[str, ...]:
        if self.call_path:
            return tuple(str(part).strip() for part in self.call_path if str(part).strip())
        if self.method_name:
            return (str(self.method_name).strip(),)
        return ()


@dataclass(frozen=True)
class RecordedCall:
    dependency_fqn: str
    method_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    call_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatefulProtocolMap:
    dependency_fqn: str
    operations: dict[str, str]
    entity_name: str = "entity"
    id_field: str = "id"
    strict: bool = True


class StrictDoubleError(AssertionError):
    pass


class StatefulProtocolError(StrictDoubleError):
    pass


class ProbeDouble:
    def __init__(self, dependency_fqn: str, *, state: Optional[dict[str, Any]] = None) -> None:
        self.dependency_fqn = dependency_fqn
        self.state = state if state is not None else {}
        self.calls: list[RecordedCall] = []

    def __getattr__(self, method_name: str):
        def _method(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(
                RecordedCall(
                    dependency_fqn=self.dependency_fqn,
                    method_name=method_name,
                    args=args,
                    kwargs=dict(kwargs),
                    call_path=(method_name,),
                )
            )
            return None

        return _method


class StrictDouble:
    def __init__(
        self,
        protocol: DependencyProtocol,
        *,
        behaviors: Optional[list[DependencyBehavior]] = None,
        state: Optional[dict[str, Any]] = None,
        _root: "StrictDouble | None" = None,
        _protocol_path: tuple[str, ...] = (),
        _depth: int = 0,
    ) -> None:
        self.protocol = protocol
        self.state = state if state is not None else {}
        self.calls: list[RecordedCall] = []
        self._root = _root or self
        self._protocol_path = _protocol_path
        self._depth = _depth
        self._methods = {method.method_name: method for method in protocol.methods}
        self._nested_protocols = protocol.nested()
        self._children: dict[str, StrictDouble] = {}

        if self is self._root:
            self._behaviors: DefaultDict[tuple[str, ...], list[DependencyBehavior]] = defaultdict(list)
            for behavior in behaviors or []:
                if behavior.dependency_fqn != protocol.dependency_fqn:
                    continue
                self.configure_behavior(behavior)
        else:
            self._behaviors = self._root._behaviors

    def configure_behavior(self, behavior: DependencyBehavior) -> None:
        if behavior.dependency_fqn != self._root.protocol.dependency_fqn:
            raise StrictDoubleError(
                f"Behavior dependency '{behavior.dependency_fqn}' does not match '{self._root.protocol.dependency_fqn}'"
            )
        call_path = behavior.normalized_call_path()
        if not call_path:
            raise StrictDoubleError("Dependency behavior requires method_name or call_path")
        self._root._validate_call_path(call_path)
        self._root._behaviors[call_path].append(behavior)

    def configure_return(self, method_name: str, value: Any) -> None:
        self.configure_behavior(
            DependencyBehavior(
                dependency_fqn=self._root.protocol.dependency_fqn,
                method_name=method_name,
                call_path=self._normalize_relative_call_path(method_name),
                action="return",
                value=value,
            )
        )

    def configure_raise(self, method_name: str, error: BaseException | str) -> None:
        call_path = self._normalize_relative_call_path(method_name)
        if isinstance(error, BaseException):
            behavior = DependencyBehavior(
                dependency_fqn=self._root.protocol.dependency_fqn,
                method_name=method_name,
                call_path=call_path,
                action="raise",
                value=error,
                exception_type=type(error).__name__,
                exception_message=str(error),
            )
        else:
            behavior = DependencyBehavior(
                dependency_fqn=self._root.protocol.dependency_fqn,
                method_name=method_name,
                call_path=call_path,
                action="raise",
                value=None,
                exception_type="RuntimeError",
                exception_message=str(error),
            )
        self.configure_behavior(behavior)

    def configure_async_return(self, method_name: str, value: Any) -> None:
        self.configure_behavior(
            DependencyBehavior(
                dependency_fqn=self._root.protocol.dependency_fqn,
                method_name=method_name,
                call_path=self._normalize_relative_call_path(method_name),
                action="async_return",
                value=value,
            )
        )

    def _normalize_relative_call_path(self, method_name: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(method_name, (list, tuple)):
            suffix = tuple(str(part).strip() for part in method_name if str(part).strip())
        else:
            suffix = (str(method_name).strip(),)
        return tuple(self._protocol_path) + suffix

    def _validate_method(self, method_name: str) -> MethodProtocol:
        method = self._methods.get(method_name)
        if method is None:
            raise StrictDoubleError(
                f"Method {method_name!r} not allowed in StrictDouble for {self.protocol.dependency_fqn or self.protocol.protocol_id}"
            )
        return method

    def _validate_call_path(self, call_path: tuple[str, ...]) -> None:
        if len(call_path) > MAX_DEPENDENCY_CHAIN_DEPTH:
            raise StrictDoubleError(
                f"Dependency call chain exceeds MAX_DEPENDENCY_CHAIN_DEPTH={MAX_DEPENDENCY_CHAIN_DEPTH}: {call_path}"
            )

        current_protocol = self.protocol
        for index, segment in enumerate(call_path):
            method_map = {method.method_name: method for method in current_protocol.methods}
            method = method_map.get(segment)
            if method is None:
                raise StrictDoubleError(
                    f"Method {segment!r} not allowed in protocol {current_protocol.protocol_id or current_protocol.dependency_fqn}"
                )
            if index < len(call_path) - 1:
                if not method.returns_protocol:
                    raise StrictDoubleError(f"Method {segment!r} does not return a nested protocol")
                nested = current_protocol.nested().get(method.returns_protocol)
                if nested is None:
                    raise StrictDoubleError(
                        f"Nested protocol {method.returns_protocol!r} missing for method {segment!r}"
                    )
                current_protocol = nested

    def __getattr__(self, method_name: str):
        method_protocol = self._methods.get(method_name)
        if method_protocol is None:
            if self.protocol.strict:
                raise StrictDoubleError(
                    f"Unexpected method '{method_name}' for dependency {self.protocol.dependency_fqn or self.protocol.protocol_id}"
                )
            return self._build_fallback_method(method_name, is_async=False)

        return self._build_method(method_protocol)

    def _build_fallback_method(self, method_name: str, *, is_async: bool):
        if is_async:

            async def _async_method(*args: Any, **kwargs: Any) -> Any:
                self._record_call(method_name, args, kwargs, call_path=self._protocol_path + (method_name,))
                return None

            return _async_method

        def _method(*args: Any, **kwargs: Any) -> Any:
            self._record_call(method_name, args, kwargs, call_path=self._protocol_path + (method_name,))
            return None

        return _method

    def _build_method(self, method: MethodProtocol):
        if method.is_async:

            async def _async_method(*args: Any, **kwargs: Any) -> Any:
                call_path = self._protocol_path + (method.method_name,)
                self._record_call(method.method_name, args, kwargs, call_path=call_path)
                return self._handle_invocation(method, call_path)

            return _async_method

        def _method(*args: Any, **kwargs: Any) -> Any:
            call_path = self._protocol_path + (method.method_name,)
            self._record_call(method.method_name, args, kwargs, call_path=call_path)
            return self._handle_invocation(method, call_path)

        return _method

    def _record_call(
        self,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        call_path: tuple[str, ...],
    ) -> None:
        self._root.calls.append(
            RecordedCall(
                dependency_fqn=self._root.protocol.dependency_fqn,
                method_name=method_name,
                args=args,
                kwargs=dict(kwargs),
                call_path=call_path,
            )
        )

    def _handle_invocation(self, method: MethodProtocol, call_path: tuple[str, ...]) -> Any:
        if len(call_path) > MAX_DEPENDENCY_CHAIN_DEPTH:
            raise StrictDoubleError(
                f"Dependency call chain exceeds MAX_DEPENDENCY_CHAIN_DEPTH={MAX_DEPENDENCY_CHAIN_DEPTH}: {call_path}"
            )

        if method.returns_protocol:
            nested = self._nested_protocols.get(method.returns_protocol)
            if nested is None:
                raise StrictDoubleError(
                    f"Nested protocol {method.returns_protocol!r} missing for method {method.method_name!r}"
                )
            child = self._children.get(method.method_name)
            if child is None:
                child = StrictDouble(
                    nested,
                    state=self.state,
                    _root=self._root,
                    _protocol_path=call_path,
                    _depth=self._depth + 1,
                )
                self._children[method.method_name] = child
            return child

        return self._execute_behavior(call_path, is_async=method.is_async)

    def _execute_behavior(self, call_path: tuple[str, ...], *, is_async: bool) -> Any:
        queue = self._root._behaviors.get(call_path, [])
        if not queue:
            raise StrictDoubleError(
                f"No behavior configured for call path {call_path!r} in dependency {self._root.protocol.dependency_fqn}"
            )

        behavior = queue[0]
        if len(queue) > 1:
            behavior = queue.pop(0)

        if behavior.action == "return":
            return behavior.value
        if behavior.action == "async_return":
            return behavior.value
        if behavior.action == "raise":
            if isinstance(behavior.value, BaseException):
                raise behavior.value
            exception_type = str(behavior.exception_type or "RuntimeError")
            exception_message = str(behavior.exception_message or "configured strict double failure")
            exc_cls = getattr(__builtins__, exception_type, RuntimeError)
            if not isinstance(exc_cls, type) or not issubclass(exc_cls, BaseException):
                exc_cls = RuntimeError
            raise exc_cls(exception_message)
        if behavior.action == "yield":
            return _StatefulYieldIterator(behavior.value, self.state)
        raise StrictDoubleError(f"Unsupported action '{behavior.action}' for call path '{call_path}'")

    def assert_called_methods(self) -> list[str]:
        return [call.method_name for call in self._root.calls]


class InMemoryStatefulDouble:
    _SUPPORTED_OPERATIONS = {"create", "get", "get_by_id", "list", "update", "delete", "exists"}

    def __init__(
        self,
        protocol_map: StatefulProtocolMap,
        *,
        state: Optional[dict[str, Any]] = None,
    ) -> None:
        self.protocol_map = protocol_map
        self.state = state if state is not None else {}
        self.calls: list[RecordedCall] = []
        self._items: dict[Any, dict[str, Any]] = {}
        self._next_id = 1
        self._operation_by_method = self._validate_protocol_map(protocol_map)
        self.state.setdefault("items", self._items)
        self.state.setdefault("next_id", self._next_id)
        self.state.setdefault("protocol_map", dict(protocol_map.operations))

    def _validate_protocol_map(self, protocol_map: StatefulProtocolMap) -> dict[str, str]:
        if not isinstance(protocol_map.operations, dict) or not protocol_map.operations:
            raise StatefulProtocolError("Stateful protocol map requires at least one semantic operation")

        reverse: dict[str, str] = {}
        for observed_method, semantic_operation in protocol_map.operations.items():
            method_name = str(observed_method or "").strip()
            semantic_name = str(semantic_operation or "").strip().lower()
            if not method_name or not semantic_name:
                raise StatefulProtocolError("Protocol map entries require method name and semantic operation")
            if semantic_name not in self._SUPPORTED_OPERATIONS:
                raise StatefulProtocolError(f"Unsupported semantic operation: {semantic_name}")
            if method_name in reverse:
                raise StatefulProtocolError(f"Duplicate method mapping detected: {method_name}")
            reverse[method_name] = semantic_name

        if "create" not in reverse.values():
            raise StatefulProtocolError("Stateful protocol requires a create operation")
        return reverse

    def __getattr__(self, method_name: str):
        semantic_operation = self._operation_by_method.get(method_name)
        if semantic_operation is None:
            if self.protocol_map.strict:
                raise StatefulProtocolError(
                    f"Unexpected stateful method '{method_name}' for dependency {self.protocol_map.dependency_fqn}"
                )
            return self._build_unknown_method(method_name)
        return self._build_method(method_name, semantic_operation)

    def _build_unknown_method(self, method_name: str):
        def _method(*args: Any, **kwargs: Any) -> Any:
            self._record_call(method_name, args, kwargs)
            raise StatefulProtocolError(f"Unhandled stateful method '{method_name}' invoked without semantic mapping")

        return _method

    def _build_method(self, method_name: str, semantic_operation: str):
        def _method(*args: Any, **kwargs: Any) -> Any:
            self._record_call(method_name, args, kwargs)
            return self._dispatch(semantic_operation, args, kwargs)

        return _method

    def _record_call(self, method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append(
            RecordedCall(
                dependency_fqn=self.protocol_map.dependency_fqn,
                method_name=method_name,
                args=args,
                kwargs=dict(kwargs),
                call_path=(method_name,),
            )
        )

    def _dispatch(self, semantic_operation: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if semantic_operation == "create":
            return self._create(args, kwargs)
        if semantic_operation in {"get", "get_by_id"}:
            return self._get(args, kwargs)
        if semantic_operation == "list":
            return self._list()
        if semantic_operation == "update":
            return self._update(args, kwargs)
        if semantic_operation == "delete":
            return self._delete(args, kwargs)
        if semantic_operation == "exists":
            return self._exists(args, kwargs)
        raise StatefulProtocolError(f"Unsupported semantic operation: {semantic_operation}")

    def _create(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = self._coerce_payload(args, kwargs)
        identifier = payload.get(self.protocol_map.id_field)
        if identifier is None:
            identifier = self._next_id
            self._next_id += 1
            self.state["next_id"] = self._next_id
        item = dict(payload)
        item[self.protocol_map.id_field] = identifier
        self._items[identifier] = item
        return dict(item)

    def _get(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[dict[str, Any]]:
        identifier = self._coerce_identifier(args, kwargs)
        item = self._items.get(identifier)
        return dict(item) if isinstance(item, dict) else None

    def _list(self) -> list[dict[str, Any]]:
        return [dict(item) for _, item in sorted(self._items.items(), key=lambda pair: pair[0])]

    def _update(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[dict[str, Any]]:
        identifier = self._coerce_identifier(args, kwargs)
        if identifier not in self._items:
            return None
        payload = self._coerce_payload(args[1:] if args else (), kwargs, allow_empty=True)
        current = dict(self._items[identifier])
        current.update(payload)
        current[self.protocol_map.id_field] = identifier
        self._items[identifier] = current
        return dict(current)

    def _delete(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        identifier = self._coerce_identifier(args, kwargs)
        if identifier not in self._items:
            return False
        del self._items[identifier]
        return True

    def _exists(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        identifier = self._coerce_identifier(args, kwargs)
        return identifier in self._items

    def _coerce_identifier(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if args:
            return args[0]
        candidate_keys = [
            self.protocol_map.id_field,
            f"{self.protocol_map.entity_name}_id",
            "id",
            "item_id",
        ]
        for key in candidate_keys:
            if key in kwargs:
                return kwargs[key]
        raise StatefulProtocolError("Stateful operation requires an identifier argument")

    def _coerce_payload(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        if args and isinstance(args[0], dict):
            return dict(args[0])
        for key in ("payload", "data", self.protocol_map.entity_name):
            value = kwargs.get(key)
            if isinstance(value, dict):
                return dict(value)
        filtered = {
            key: value
            for key, value in kwargs.items()
            if key not in {self.protocol_map.id_field, f"{self.protocol_map.entity_name}_id", "id", "item_id"}
        }
        if filtered or allow_empty:
            return filtered
        raise StatefulProtocolError("Stateful create/update requires a dictionary-like payload")

    def assert_called_methods(self) -> list[str]:
        return [call.method_name for call in self.calls]


class _StatefulYieldIterator:
    def __init__(self, value: Any, state: dict[str, Any]) -> None:
        self._value = value
        self._state = state
        self._consumed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._consumed:
            raise StopIteration
        self._consumed = True
        if isinstance(self._value, tuple) and len(self._value) == 2 and isinstance(self._value[0], str):
            self._state[self._value[0]] = self._value[1]
            return self._value[1]
        if inspect.isgenerator(self._value):
            return next(self._value)
        return self._value
