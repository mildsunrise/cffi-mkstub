from typing import SupportsIndex, TypeAlias, Self, Iterator, Union, Any, Literal, Callable, Protocol, Optional, overload, final
from _typeshed import ReadableBuffer
from types import TracebackType
import _cffi_backend
# needed for conditional definitions in FFI, see below
import sys

class CField:
	bitshift: int
	bitsize: int
	flags: int
	offset: int
	type: 'CType'

class CTypeEnum(Protocol):
	cname: str
	kind: Literal['enum']
	relements: dict[str, int]
	elements: dict[int, str]

class CTypePrimitive(Protocol):
	cname: str
	kind: Literal['primitive']

class CTypePointer(Protocol):
	cname: str
	kind: Literal['pointer']
	item: 'CType'

class CTypeArray(Protocol):
	cname: str
	kind: Literal['array']
	item: 'CType'
	length: Optional[int]
	''' amount of elements if known (`T[N]`), or None if unknown (`T[]`) '''

class CTypeVoid(Protocol):
	cname: Literal['void']
	kind: Literal['void']

class CTypeStruct(Protocol):
	cname: str
	kind: Literal['struct']
	fields: Optional[list[tuple[str, CField]]]

class CTypeUnion(Protocol):
	cname: str
	kind: Literal['union']
	fields: Optional[list[tuple[str, CField]]]

''' function pointer '''
class CTypeFunction(Protocol):
	cname: str
	kind: Literal['function']
	abi: int
	args: tuple['CType', ...]
	ellipsis: bool
	result: 'CType'

CType: TypeAlias = Union[CTypeEnum, CTypePrimitive, CTypePointer, CTypeArray, CTypeVoid, CTypeStruct, CTypeUnion, CTypeFunction]

class _CDataBase:
	def __enter__(self) -> Self: ...
	def __exit__(self, type: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None, /) -> None: ...

	def __bool__(self) -> bool: ...
	def __hash__(self) -> int: ...

class IntCData(_CDataBase):
	def __int__(self) -> int: ...
	def __eq__(self, other: IntCData, /) -> bool: ...  # type: ignore[override]
	def __ne__(self, other: IntCData, /) -> bool: ...  # type: ignore[override]
	def __ge__(self, other: IntCData, /) -> bool: ...
	def __gt__(self, other: IntCData, /) -> bool: ...
	def __le__(self, other: IntCData, /) -> bool: ...
	def __lt__(self, other: IntCData, /) -> bool: ...

class IntPrimitive(IntCData):
	pass

class EnumCData(IntCData):
	pass

class FunctionCData(_CDataBase):
	def __call__(self, *args: InValue) -> OutValue | None: ...

class CompositeCData(_CDataBase):
	pass

class FloatPrimitive(_CDataBase):
	def __int__(self) -> int: ...
	def __float__(self) -> float: ...
	def __eq__(self, other: FloatPrimitive, /) -> bool: ...  # type: ignore[override]
	def __ne__(self, other: FloatPrimitive, /) -> bool: ...  # type: ignore[override]
	def __ge__(self, other: FloatPrimitive, /) -> bool: ...
	def __gt__(self, other: FloatPrimitive, /) -> bool: ...
	def __le__(self, other: FloatPrimitive, /) -> bool: ...
	def __lt__(self, other: FloatPrimitive, /) -> bool: ...

class ComplexPrimitive(_CDataBase):
	def __complex__(self) -> complex: ...
	def __eq__(self, other: ComplexPrimitive, /) -> bool: ...  # type: ignore[override]
	def __ne__(self, other: ComplexPrimitive, /) -> bool: ...  # type: ignore[override]
	def __ge__(self, other: ComplexPrimitive, /) -> bool: ...
	def __gt__(self, other: ComplexPrimitive, /) -> bool: ...
	def __le__(self, other: ComplexPrimitive, /) -> bool: ...
	def __lt__(self, other: ComplexPrimitive, /) -> bool: ...

class PointerBase[T](_CDataBase):
	""" Used for all pointers, including those pointing to unsized types, which have restricted operations. """
	def __eq__(self, other: PointerBase[Any], /) -> bool: ...  # type: ignore[override]
	def __ne__(self, other: PointerBase[Any], /) -> bool: ...  # type: ignore[override]
	def __ge__(self, other: PointerBase[Any], /) -> bool: ...
	def __gt__(self, other: PointerBase[Any], /) -> bool: ...
	def __le__(self, other: PointerBase[Any], /) -> bool: ...
	def __lt__(self, other: PointerBase[Any], /) -> bool: ...

class Pointer[T](PointerBase[T]):
	""" Pointer to a sized type. """
	def __add__(self, other: int, /) -> Pointer[T]: ...
	def __radd__(self, other: int, /) -> Pointer[T]: ...
	@overload
	def __sub__(self, other: int, /) -> Pointer[T]: ...
	@overload
	def __sub__(self, other: Pointer[T], /) -> int: ...
	# FIXME: make cffi actually raise in the unsized case
	@overload
	def __getitem__(self, index: int) -> T: ...
	@overload
	def __getitem__(self, index: slice) -> Array[T]: ...
	@overload
	def __setitem__(self, index: int, value: T) -> None: ...
	@overload
	def __setitem__(self, index: slice, value: Array[T]) -> None: ...

class Array[T](Pointer[T]):
	def __len__(self) -> int: ...
	def __iter__(self) -> Iterator[T]: ...

@final
class buffer:
	__hash__: ClassVar[None]  # type: ignore[assignment]
	def __new__(cls, cdata: PointerBase[object], size: int = -1) -> Self: ...
	def __buffer__(self, flags: int, /) -> memoryview: ...
	def __eq__(self, other: ReadableBuffer, /) -> bool: ...  # type: ignore[override]
	def __ne__(self, other: ReadableBuffer, /) -> bool: ...  # type: ignore[override]
	def __ge__(self, other: ReadableBuffer, /) -> bool: ...
	def __gt__(self, other: ReadableBuffer, /) -> bool: ...
	def __le__(self, other: ReadableBuffer, /) -> bool: ...
	def __lt__(self, other: ReadableBuffer, /) -> bool: ...
	def __len__(self) -> int: ...
	def __getitem__(self, index: Union[SupportsIndex, slice], /) -> bytes: ...
	def __setitem__(self, index: Union[SupportsIndex, slice], value: bytes, /) -> None: ...

# These aliases are to work around pyright complaints.
# Pyright doesn't like it when a class object is defined as an alias
# of a global object with the same name.
_tmp_buffer = buffer

ErrorCallback: TypeAlias = Callable[[Exception, Any, TracebackType], Any]

OutValue: TypeAlias = Union[PointerBase[Any], FunctionCData, CompositeCData, int, bool, float, FloatPrimitive, complex, bytes, str]
InValue: TypeAlias = Union[_CDataBase, OutValue]
