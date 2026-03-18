import os.path
from typing import Union, Literal, NoReturn, TypeAlias, TYPE_CHECKING
import re
from dataclasses import dataclass
import textwrap
import _cffi_backend
if TYPE_CHECKING:
	from cffi_types import FFI, CType, CGlobal
else:
	FFI = _cffi_backend.FFI
	CType = CGlobal = None

__all__ = ['format_type_hints']

_dirname = os.path.dirname(__file__)

def assert_never(arg: NoReturn) -> NoReturn:
	raise AssertionError("Expected code to be unreachable")

# imports to put at the top of our type hints.
with open(os.path.join(_dirname, '_stub_preamble.pyi')) as f:
	IMPORTS = f.read()

# definitions of FFI that reference Lib, and thus we need to reproduce in our
# type hints so that Lib points to the right type. copied from typeshed.
# in the future, FFI would ideally be made generic in Lib and this would no
# longer be needed
FFI_DEFS = '''
CData: TypeAlias = _CDataBase

def dlclose(self, lib: Lib, /) -> None: ...
if sys.platform == "win32":
	def dlopen(self, libpath: str | FFI.CData, flags: int = ..., /) -> Lib: ...
else:
	def dlopen(self, libpath: str | FFI.CData | None = ..., flags: int = ..., /) -> Lib: ...

def gc[T: CData](self, cdata: T, destructor: Callable[[T], Any], size: int = ...) -> T: ...
'''.strip()

@dataclass(frozen=True)
class PrimitiveData:
	ident_name: Union[str, None]
	''' name of the type alias to define (if None -> no type alias, expr is used directly) '''
	expr: str
	''' python type to reference '''
	docs: Union[str, None]
	''' docstring to add to the type alias, if any '''

# FIXME: i'm writing this from memory right now, will likely be incomplete, should check backend source
def _gen_primitives():
	dst: dict[str, PrimitiveData] = {}
	for k, v in {
		'bool': 'bool',
		'short': 'int',
		'int': 'int',
		'long': 'int',
		'unsigned short': 'int',
		'unsigned int': 'int',
		'unsigned long': 'int',
		'size_t': 'int',
		'uintptr_t': 'int',
		'intptr_t': 'int',
		'float': 'float',
		'double': 'float',
		'float _Complex': 'complex',
		'double _Complex': 'complex',
		'char': ('bytes', 'bytes of length 1'),
		'unsigned char': ('bytes', 'bytes of length 1'),
		'uint8_t': ('bytes', 'bytes of length 1'),
		'int8_t': ('bytes', 'bytes of length 1'),
		'wchar_t': ('str', 'string of length 1'),
		'char16_t': ('str', 'string of length 1'),
		'char32_t': ('str', 'string of length 1'),
	}.items():
		docs = None
		if isinstance(v, tuple):
			v, docs = v
		ident_name = k.replace(' ', '_')
		if k == v: ident_name = None
		v = PrimitiveData(ident_name, v, docs)
		dst[k] = v
	return dst
PRIMITIVES = _gen_primitives()

# these names are (potentially) used in the types namespace for things other than typedefs, so prepend an _ if used in a typedef name
RESERVED_NAMES = [
	*{ v.ident_name for v in PRIMITIVES.values() if v.ident_name },
]
RESERVED_PREFIXES = [
	'struct', 'union', 'enum',
	'item', 'result', r'arg\d+', 'sym',
	'anon', r'arr\d*',
]
def _regex_paren(x: str): return f'(?:{x})'
def _regex_alt(*x: str): return _regex_paren('|'.join(x))
RESERVED_NAME_PATTERN = re.compile(f'_*' + _regex_alt(
	_regex_paren(_regex_alt(*RESERVED_PREFIXES) + '_.+'),
	_regex_alt(*RESERVED_NAMES),
))
def sanitize_typedef_name(name: str):
	if RESERVED_NAME_PATTERN.fullmatch(name):
		name = '_' + name
	return name

# how did we discover a CType?
TypeRef: TypeAlias = Union[
	tuple[Literal['name'], str], # through type's name (excluding 'enum', 'struct' or 'union')
	tuple[Literal['typedef'], str], # through typedef
	tuple[Literal['global'], CGlobal], # through global
	# through another type:
	tuple[Literal['pointer'], CType], # array or pointer type item
	tuple[Literal['field'], CType, int], # struct/union field (field index)
	tuple[Literal['return'], CType], # function pointer return type
	tuple[Literal['arg'], CType, int], # function pointer argument (arg index)
]


def format_type_hints(
	ffi: FFI,
	root_cls_name: str = 'FFI',
	lib_cls_name: str = 'Lib',
	types_ns_name: str = 'types',
	indent: str = ' '*4, # pyright: ignore[reportRedeclaration]
) -> str:
	'''
	Formats stub python code for a given `ffi` object.
	'''

	_indent_prefix = indent
	def indent(text: str):
		return textwrap.indent(text, _indent_prefix)
	def gen_type_alias(name: str, expr: str):
		return f'{name}: TypeAlias = {expr}'

	assert isinstance(ffi, FFI), f'{ffi!r} is not a cffi FFI object'

	# visit all CTypes, while storing backreferences, starting from globals and named types
	# ----

	ctypes: dict[str, tuple[CType, list[TypeRef]]] = {}
	type_size: dict[str, Union[int, None]] = {}
	def add_ctype(ct: CType, ref: Union[TypeRef, None], visit: bool = True):
		cname = ct.cname
		if cname in ctypes:
			assert ctypes[cname][0] is ct
			if ref: ctypes[cname][1].append(ref)
			return
		ctypes[cname] = ct, []
		if ref: ctypes[cname][1].append(ref)

		try:
			size = ffi.sizeof(cname)
		except ffi.error:
			size = None
		type_size[cname] = size

		if not visit: return

		if ct.kind == 'pointer' or ct.kind == 'array':
			add_ctype(ct.item, ('pointer', ct))
		elif ct.kind == 'enum' or ct.kind == 'primitive' or ct.kind == 'void':
			pass
		elif ct.kind == 'struct' or ct.kind == 'union':
			for i, (_, field) in enumerate(ct.fields or []):
				add_ctype(field.type, ('field', ct, i))
		elif ct.kind == 'function':
			add_ctype(ct.result, ('return', ct))
			for i, arg in enumerate(ct.args):
				add_ctype(arg, ('arg', ct, i))
		else:
			assert_never(ct)
		# order ourselves after our dependencies, for code generation
		ctypes[cname] = ctypes.pop(cname)

		if type_size[cname] != None:
			# register pointer and unsized array to this type, but do not visit them (that'd cause infinite recursion)
			add_ctype(pct := _cffi_backend.new_pointer_type(ct), None, visit=False)
			add_ctype(_cffi_backend.new_array_type(pct, None), None, visit=False)

	typedefs, structs, unions = ffi.list_types()
	for name in typedefs:
		try:
			add_ctype(ffi.typeof(name), ('typedef', name))
		except ffi.error as exc:
			# finicky, but cffi doesn't really give us anything better
			if not ('is a function type, not a pointer-to-function type' in exc.args[0]):
				raise
	for name in structs:
		add_ctype(ffi.typeof('struct ' + name), ('name', name))
	for name in unions:
		add_ctype(ffi.typeof('union ' + name), ('name', name))
	for name in ffi.list_enums():
		add_ctype(ffi.typeof('enum ' + name), ('name', name))
	for cglobal in ffi.list_globals():
		if cglobal.kind == 'int_constant' or cglobal.kind == 'enum':
			pass
		elif cglobal.kind == 'function' or cglobal.kind == 'python_function' or cglobal.kind == 'constant' or cglobal.kind == 'variable':
			add_ctype(cglobal.type, ('global', cglobal))
		else:
			assert_never(cglobal)

	# generate code for the types, as well as python expressions to refer to them
	# ----

	type_exprs: dict[str, str] = {}

	cdata_type = '_CDataBase'
	vararg_type = 'VarArg'
	def types_ident(x: str): return types_ns_name + '.' + x

	types_defs: list[str] = []

	# first pass to emit any referenced primitive types
	for cname, (ct, _) in ctypes.items():
		if ct.kind != 'primitive':
			continue
		pdata = PRIMITIVES.get(cname)
		assert pdata, f'no primitive {cname!r} defined, please open an issue'
		type_expr = pdata.expr
		if pdata.ident_name != None:
			docs = f"\n''' {pdata.docs} '''" if pdata.docs else ''
			types_defs.append(gen_type_alias(pdata.ident_name, pdata.expr) + docs)
			type_expr = types_ident(pdata.ident_name)
		type_exprs[cname] = type_expr

	anon_counters = { 'struct': 0, 'union': 0, 'function': 0 }

	for cname, (ct, brefs) in ctypes.items():
		typedefs = [ bref[1] for bref in brefs if bref[0] == 'typedef' ]
		globals = [ bref[1].name for bref in brefs if bref[0] == 'global' ]

		# extract the type's name, if any (by C rules, it must be unique)
		type_name = [ bref[1] for bref in brefs if bref[0] == 'name' ]
		assert len(type_name) <= 1
		type_name = type_name[0] if type_name else None

		if ct.kind == 'primitive':
			type_expr = type_exprs[cname] # already processed, we just need the typedefs
		elif ct.kind == 'struct' or ct.kind == 'union':
			if type_name:
				type_expr = ct.kind + '_' + type_name
			elif len(typedefs) == 1:
				# because of C rules, we positively know that this anonymous struct was defined in this typedef
				type_expr = sanitize_typedef_name(typedefs[0])
				typedefs = []
			else:
				anon_counters[ct.kind] = n = anon_counters[ct.kind] + 1
				type_expr = f'anon_{ct.kind}_{n}'
			cls_defs = [ name + ': ' + type_exprs[field.type.cname]
				for name, field in ct.fields or [] ]
			types_defs.append(f'class {type_expr}({cdata_type}):\n' + indent('\n'.join(cls_defs or ['pass'])))
			type_expr = types_ident(type_expr)
		elif ct.kind == 'void':
			assert cname == 'void' # special case: we'll handle it where it appears
			continue
		elif ct.kind == 'pointer' or ct.kind == 'array':
			if (size := type_size[ct.item.cname]) == None:
				assert ct.kind == 'pointer'
			if ct.item.kind == 'void':
				type_expr = 'object'
			else:
				type_expr = type_exprs[ct.item.cname]
			base = 'Array' if ct.kind == 'array' else \
				'Pointer' if size != None else 'PointerBase'
			type_expr = f'{base}[{type_expr}]'
		elif ct.kind == 'function':
			if len(typedefs) == 1:
				ident = sanitize_typedef_name(typedefs[0])
				typedefs = []
			elif len(globals) == 1:
				ident = 'sym_' + globals[0]
			else:
				anon_counters[ct.kind] = n = anon_counters[ct.kind] + 1
				ident = f'anon_funcptr_{n}'
			result = 'None' if ct.result.kind == 'void' else type_exprs[ct.result.cname]
			arg_exprs = ['self']
			arg_exprs.extend( f'arg{i}: {type_exprs[arg.cname]}' for i, arg in enumerate(ct.args) )
			arg_exprs.append('/')
			if ct.ellipsis: arg_exprs.append(f'*args: {vararg_type}')
			cls_defs = f'def __call__({", ".join(arg_exprs)}) -> {result}: ...'
			types_defs.append(f'class {ident}({cdata_type}):\n' + indent(cls_defs))
			type_expr = types_ident(ident)
		elif ct.kind == 'enum':
			type_expr = 'int'
			if type_name:
				type_expr = 'enum_' + type_name
				types_defs.append(gen_type_alias(type_expr, 'int'))
				type_expr = types_ident(type_expr)
		else:
			assert_never(ct)

		type_exprs[cname] = type_expr

		for typedef in typedefs:
			types_defs.append(gen_type_alias(sanitize_typedef_name(typedef), type_expr))

	# generate code for globals

	lib_cls_defs: list[str] = []

	cglobals = { cg.name: cg for cg in ffi.list_globals() }
	assert len(cglobals) == len(ffi.list_globals())

	enum_elements: dict[str, CType] = {}
	for ct, _ in ctypes.values():
		if ct.kind == 'enum':
			for name, value in ct.relements.items():
				assert name not in enum_elements
				enum_elements[name] = ct
				cg = cglobals[name]
				assert cg.kind == 'enum' and cg.value == value

	for name, cglobal in cglobals.items():
		if cglobal.kind == 'int_constant' or cglobal.kind == 'enum':
			type_expr = 'int'
			if cglobal.kind == 'enum':
				type_expr = type_exprs[enum_elements[name].cname]
			lib_cls_defs.append(name + ': ' + type_expr + ' = ' + str(cglobal.value))
		elif cglobal.kind == 'function' or cglobal.kind == 'python_function' or cglobal.kind == 'constant':
			type_expr = type_exprs[cglobal.type.cname]
			lib_cls_defs.append('@property\n' f'def {name}(self) -> {type_expr}:\n' + indent('...'))
		elif cglobal.kind == 'variable':
			type_expr = type_exprs[cglobal.type.cname]
			lib_cls_defs.append(name + ': ' + type_expr + ' = ...')
		else:
			assert_never(cglobal)

	# generate the other APIs

	ffi_defs = [FFI_DEFS.replace('\t', _indent_prefix)]

	def def_overloaded(name: str, *overloads: tuple[str, str] | tuple[str, str, str]):
		''' declares an overloaded function, given a series of ([generic params], args, result) tuples '''
		if not overloads: return
		fdef = '\n'.join('@overload\n' f'def {name}{f"[{o[0]}]" if len(o) == 3 else ""}({o[-2]}) -> {o[-1]}: ...' for o in overloads)
		ffi_defs.append(fdef)

	def ctype_expr(ct: CType) -> str:
		typedefs = [ bref[1] for bref in ctypes[ct.cname][1] if bref[0] == 'typedef' ]
		names = [ct.cname] + typedefs
		return f'Literal[{", ".join(map(repr, names))}]'

	def initializer_expr(ct: CType):
		if ct.kind != 'pointer' and ct.kind != 'array':
			return
		if type_size[ct.item.cname] == None:
			return
		item_init = type_exprs[ct.item.cname]
		if ct.kind == 'pointer':
			return f'{item_init} = ...'
		init = f'tuple[{item_init}, ...]'
		if ct.item.cname in {'char', 'unsigned char', 'uint8_t', 'int8_t'}:
			init = f'Union[bytes, {init}]'
		if ct.item.cname in {'wchar_t', 'char16_t', 'char32_t'}:
			init = f'Union[str, {init}]'
		if ct.length != None:
			if ct.length <= 5:
				return f'tuple[{", ".join([item_init] * ct.length)}] = ...'
			return f'{init} = ...'
		return f'Union[int, {init}]'

	def_overloaded('new',
		*( (f'self, ctype: {ctype_expr(ct)}, init: {init}, /', type_exprs[ct.cname])
			for ct, _ in ctypes.values() if (init := initializer_expr(ct)) != None ),
	)

	def callable_expr(ct: CType):
		if ct.kind != 'function' or ct.ellipsis:
			return
		result_type = 'None' if ct.result.kind == 'void' else type_exprs[ct.result.cname]
		arg_types = [ type_exprs[arg.cname] for arg in ct.args ]
		return f'Callable[[{", ".join(arg_types)}], {result_type}]'

	def_overloaded('def_extern',
		*( (f'self, name: Literal[{name!r}], error: Any = ..., onerror: ErrorCallback = ...', f'Callable[[{cexpr}], {cexpr}]')
			for name, cg in cglobals.items() if cg.kind == 'python_function' and (cexpr := callable_expr(cg.type)) )
	)

	def_overloaded('callback',
		*( (f'self, ctype: {ctype_expr(ct)}, error: Any = ..., onerror: ErrorCallback = ...',
	  		f'Callable[[{cexpr}], {type_exprs[ct.cname]}]')
			for ct, _ in ctypes.values() if (cexpr := callable_expr(ct)) != None )
	)

	def_overloaded('cast',
		# this is here exclusively to provide an API to obtain numeric value from pointer.
		# it's intended to be immediately followed with int(), since IntPrimitive isn't actually accepted anywhere (int is)
		("self, cdata: Literal['uintptr_t', 'intptr_t', 'size_t'], value: Union[int, PointerBase[Any]], /", 'IntPrimitive'),
		# FIXME: for now this only supports int and pointer types
		*( (f'self, ctype: {ctype_expr(ct)}, value: Union[int, PointerBase[Any]], /', type_exprs[ct.cname])
			for ct, _ in ctypes.values() if ct.kind == 'pointer' or ct.kind == 'function' ),
	)

	composites = [ ct for ct, _ in ctypes.values() if ct.kind == 'struct' or ct.kind == 'union' ]
	def_overloaded('addressof',
		(f'T: {vararg_type}', 'self, cdata: Pointer[T], index: int, /', 'Pointer[T]'),
		# presumably only sized cdata objects can exist in the wild, so this is guaranteed
		# to return a pointer to sized type and we don't have to restrict it
		(f'T: {cdata_type}', 'self, cdata: T, /', 'Pointer[T]'),
		# specialized signatures for struct/union fields
		*( (f'self, cdata: {type_exprs[ct.cname]}, field: Literal[{name!r}], /',
	  		('Pointer' if type_size[ct.cname] != None else 'PointerBase') + '[' + type_exprs[field.type.cname] + ']')
			for ct in composites for name, field in ct.fields or [] )
	)

	types_ns_out = f'class {types_ns_name}:\n{indent("\n\n".join(types_defs or ["pass"]))}'
	lib_cls_out = f'class {lib_cls_name}:\n{indent("\n\n".join(lib_cls_defs or ["pass"]))}'
	ffi_cls_out = f'class {root_cls_name}(_cffi_backend.FFI):\n{indent("\n\n".join(ffi_defs))}'
	return f'{IMPORTS}\n\n{types_ns_out}\n\n{lib_cls_out}\n\n{ffi_cls_out}'
