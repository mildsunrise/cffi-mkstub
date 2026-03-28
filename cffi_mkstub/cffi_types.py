'''
More precise definitions of the introspection API (CType) than the ones currently found in typeshed.
Once/if my PR adding these APIs lands in a release, I should contribute these to typeshed if possible.
'''

from typing import Union, Literal, TypeAlias, Protocol, Optional
import _cffi_backend
from _stub_preamble import CType, CTypeFunction

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
	type: CTypeFunction

class _CGlobalPythonFunc(Protocol):
	name: str
	kind: Literal['python_function']
	type: CTypeFunction

CGlobal: TypeAlias = Union[_CGlobalInt, _CGlobalEnum, _CGlobalConstant, _CGlobalVariable, _CGlobalFunc, _CGlobalPythonFunc]

class FFI(_cffi_backend.FFI):
	@property
	def includes(self) -> Optional[tuple['FFI']]:
		...
	def list_globals(self) -> list[CGlobal]:
		...
	def list_enums(self) -> list[str]:
		...
