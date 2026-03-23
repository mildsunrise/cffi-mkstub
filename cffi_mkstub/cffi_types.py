'''
More precise definitions of the introspection API (CType) than the ones currently found in typeshed.
Once/if my PR adding these APIs lands in a release, I should contribute these to typeshed if possible.
'''

from typing import Union, Literal, TypeAlias, Protocol, Optional
import _cffi_backend

class CField:
	bitshift: int
	bitsize: int
	flags: int
	offset: int
	type: 'CType'

class _CTypeEnum(Protocol):
	cname: str
	kind: Literal['enum']
	relements: dict[str, int]
	elements: dict[int, str]

class _CTypePrimitive(Protocol):
	cname: str
	kind: Literal['primitive']

class _CTypePointer(Protocol):
	cname: str
	kind: Literal['pointer']
	item: 'CType'

class _CTypeArray(Protocol):
	cname: str
	kind: Literal['array']
	item: 'CType'
	length: Optional[int]
	''' amount of elements if known (`T[N]`), or None if unknown (`T[]`) '''

class _CTypeVoid(Protocol):
	cname: Literal['void']
	kind: Literal['void']

class _CTypeStruct(Protocol):
	cname: str
	kind: Literal['struct']
	fields: Optional[list[tuple[str, CField]]]

class _CTypeUnion(Protocol):
	cname: str
	kind: Literal['union']
	fields: Optional[list[tuple[str, CField]]]

''' function pointer '''
class _CTypeFunction(Protocol):
	cname: str
	kind: Literal['function']
	abi: int
	args: tuple['CType', ...]
	ellipsis: bool
	result: 'CType'

CType: TypeAlias = Union[_CTypeEnum, _CTypePrimitive, _CTypePointer, _CTypeArray, _CTypeVoid, _CTypeStruct, _CTypeUnion, _CTypeFunction]

class _CGlobalInt(Protocol):
	name: str
	kind: Literal['int_constant']
	value: int

class _CGlobalEnum(Protocol):
	name: str
	kind: Literal['enum']
	value: int

class _CGlobalConstant(Protocol):
	name: str
	kind: Literal['constant']
	type: CType

class _CGlobalVariable(Protocol):
	name: str
	kind: Literal['variable']
	type: CType

class _CGlobalFunc(Protocol):
	name: str
	kind: Literal['function']
	type: _CTypeFunction

class _CGlobalPythonFunc(Protocol):
	name: str
	kind: Literal['python_function']
	type: _CTypeFunction

CGlobal: TypeAlias = Union[_CGlobalInt, _CGlobalEnum, _CGlobalConstant, _CGlobalVariable, _CGlobalFunc, _CGlobalPythonFunc]

class FFI(_cffi_backend.FFI):
	@property
	def includes(self) -> Optional[tuple['FFI']]:
		...
	def list_globals(self) -> list[CGlobal]:
		...
	def list_enums(self) -> list[str]:
		...
