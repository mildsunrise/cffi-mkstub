import os.path
from typing import Callable, Union, Literal, NoReturn, TypeAlias, TYPE_CHECKING, cast
import re
from dataclasses import dataclass
import textwrap

if TYPE_CHECKING:
	from cffi_types import FFI, CType, CGlobal
else:
	FFI = CType = CGlobal = None

if TYPE_CHECKING:
	import cffi
	FFI_api: TypeAlias = cffi.FFI
	import _cffi_backend
else:
	# inline ABI mode uses a different (Python backed) FFI object class
	FFI_api = None
	try:
		import cffi
		FFI_api = cffi.FFI
	except ImportError:
		pass
	# in inline ABI mode, the user can request that the ctypes backend
	# be used instead of the normal libffi-based backend (_cffi_backend)
	_cffi_backend = None
	try:
		import _cffi_backend
	except ImportError as exc:
		if not FFI_api:
			raise Exception('neither _cffi_backend nor cffi could be imported') from exc

__all__ = ['format_type_hints']

_dirname = os.path.dirname(__file__)

def assert_never(arg: NoReturn) -> NoReturn:
	raise AssertionError("Expected code to be unreachable")

class keydefaultdict[K, V](dict[K, V]):
	def __init__(self, missing: Callable[[K], V]):
		self.missing = missing
	def __missing__(self, key: K) -> V:
		return self.missing(key)

# imports to put at the top of our type hints.
with open(os.path.join(_dirname, '_stub_preamble.pyi')) as f:
	PREAMBLE = f.read()

# definitions of FFI that reference Lib, and thus we need to reproduce in our
# type hints so that Lib points to the right type. copied from typeshed.
# in the future, FFI would ideally be made generic in Lib and this would no
# longer be needed
FFI_DEFS = '''
CData: TypeAlias = _CDataBase
buffer: TypeAlias = _tmp_buffer  # noqa: Y042

NULL: PointerBase[object]

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

def _gen_primitives():
	dst: dict[str, PrimitiveData] = {}
	entry_src: dict[str, Union[str, tuple[str, str]]] = {
		'_Bool': 'bool',
		'char': ('bytes', 'bytes of length 1'),

		'signed char': 'int',
		'short': 'int',
		'int': 'int',
		'long': 'int',
		'long long': 'int',
		'unsigned char': 'int',
		'unsigned short': 'int',
		'unsigned int': 'int',
		'unsigned long': 'int',
		'unsigned long long': 'int',

		'uint8_t': 'int',
		'int8_t': 'int',
		'int16_t': 'int',
		'uint16_t': 'int',
		'int32_t': 'int',
		'uint32_t': 'int',
		'int64_t': 'int',
		'uint64_t': 'int',
		'uintptr_t': 'int',
		'intptr_t': 'int',
		'ptrdiff_t': 'int',
		'size_t': 'int',
		'ssize_t': 'int',
		'int_least8_t': 'int',
		'uint_least8_t': 'int',
		'int_least16_t': 'int',
		'uint_least16_t': 'int',
		'int_least32_t': 'int',
		'uint_least32_t': 'int',
		'int_least64_t': 'int',
		'uint_least64_t': 'int',
		'int_fast8_t': 'int',
		'uint_fast8_t': 'int',
		'int_fast16_t': 'int',
		'uint_fast16_t': 'int',
		'int_fast32_t': 'int',
		'uint_fast32_t': 'int',
		'int_fast64_t': 'int',
		'uint_fast64_t': 'int',
		'intmax_t': 'int',
		'uintmax_t': 'int',

		'float': 'float',
		'double': 'float',
		'_cffi_float_complex_t': 'complex',
		'_cffi_double_complex_t': 'complex',

		'long double': 'FloatPrimitive',

		'wchar_t': ('str', 'string of length 1'),
		'char16_t': ('str', 'string of length 1'),
		'char32_t': ('str', 'string of length 1'),
	}
	for k, v in entry_src.items():
		docs = None
		if isinstance(v, tuple):
			v, docs = v
		ident_name = k.replace(' ', '_')
		if k == v or v == 'bool': ident_name = None
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
	tuple[Literal['typedef_ptr'], str], # special case where typedef points to the function type itself, so we added the function pointer type
	tuple[Literal['global'], CGlobal], # through global
	# through another type:
	tuple[Literal['pointer'], CType], # array or pointer type item
	tuple[Literal['field'], CType, int], # struct/union field (field index)
	tuple[Literal['return'], CType], # function pointer return type
	tuple[Literal['arg'], CType, int], # function pointer argument (arg index)
]


def format_type_hints(
	ffi: Union[FFI, FFI_api],
	ffi_cls_name: str = 'FFI',
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
	def gen_literal(*values: Union[int, str, bool]):
		return f'Literal[{", ".join(map(repr, values))}]'
	def fmt_docstr(text: str):
		return f"''' {text} '''"
	def fmt_c_block(text: str):
		return '.. code-block:: c' '\n' + indent(text)

	if _cffi_backend and isinstance(ffi, _cffi_backend.FFI):
		_backend = _cffi_backend
		_error = _size_error = ffi.error
	elif FFI_api and isinstance(ffi, FFI_api):
		_backend = ffi._backend # type: ignore
		_error = cffi.CDefError
		_size_error = Exception # FIXME
	else:
		raise AssertionError(f'{ffi!r} is not a recognized cffi FFI object, perhaps it uses a different backend?')

	if TYPE_CHECKING:
		ffi = cast(FFI, ffi)
		_backend = cast(_cffi_backend, _backend)

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
		except _size_error:
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
			add_ctype(pct := _backend.new_pointer_type(ct), None, visit=False)
			add_ctype(_backend.new_array_type(pct, None), None, visit=False)

	typedefs, structs, unions = ffi.list_types()
	for name in typedefs:
		try:
			add_ctype(ffi.typeof(name), ('typedef', name))
		except _error as exc:
			# finicky, but cffi doesn't really give us anything better
			if 'is a function type, not a pointer-to-function type' in exc.args[0]:
				add_ctype(ffi.typeof(name + '*'), ('typedef_ptr', name))
			else:
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

	cname_position: dict[str, int] = {}
	# cffi does not expose the ct_name_position, but we can infer it by comparing cname with the array type cname:
	for cname, (ct, _) in ctypes.items():
		if type_size[cname] == None:
			# the method only works for sized types... make an effort for unsized arrays, as those pop up in the wild
			if ct.kind == 'array' and ct.length == None:
				cname_position[cname] = pos = cname_position[item_cname := ct.item.cname]
				assert cname == item_cname[:pos] + '[]' + item_cname[pos:]
			continue
		acname = _backend.new_array_type(_backend.new_pointer_type(ct), None).cname
		pos = [i for i in range(len(cname)+1) if acname == cname[:i] + '[]' + cname[i:]]
		assert len(pos) == 1, f'unexpected array ctype name {acname!r} from {cname!r}'
		cname_position[cname], = pos

	# generate code for the types, as well as python expressions to refer to them
	# ----

	def fmt_var(ct: CType, var_name: str, function: bool = False) -> str:
		pos = cname_position[ct.cname]
		pre, post = ct.cname[:pos], ct.cname[pos:]
		if function:
			assert ct.kind == 'function' and pre.endswith('(*') and post.startswith(')')
			pre, post = pre[:-2], post[1:]
		if pre[-1].isalnum() or pre[-1] == '_': pre += ' '
		return pre + var_name + post + ';' # FIXME: correct?

	type_exprs: dict[str, str] = keydefaultdict(lambda key: type_codegen_data[key][0])

	cdata_type = '_CDataBase'
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

	def process_type(cname: str) -> tuple[str, str, list[Callable[[], str]]]:
		ct, brefs = ctypes[cname]
		typedefs = [ bref[1] for bref in brefs if bref[0] == 'typedef' or bref[0] == 'typedef_ptr' ]
		globals = [ bref[1].name for bref in brefs if bref[0] == 'global' ]
		types_defs: list[Union[str, Callable[[], str]]] = []

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
			size = type_size[cname]
			cls_defs = [ fmt_docstr(cname + (f' (size = {size})' if size else '')) ]
			for name, field in ct.fields or []:
				docstring = '\n' + fmt_docstr('\n' + fmt_c_block(fmt_var(field.type, name)))
				cls_defs.append(name + ': ' + type_exprs[field.type.cname] + docstring)
			types_defs.append(f'class {type_expr}({cdata_type}):\n' + indent('\n'.join(cls_defs or ['pass'])))
			type_expr = types_ident(type_expr)
		elif ct.kind == 'void':
			raise AssertionError('tried to invoke codegen for void type')
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
			arg_exprs.extend( f'arg{i+1}: {type_codegen_data[arg.cname][1]}' for i, arg in enumerate(ct.args) )
			arg_exprs.append('/')
			if ct.ellipsis: arg_exprs.append(f'*args: {cdata_type}')
			docstring = fmt_docstr('function pointer type:\n' + fmt_c_block(cname)) + '\n'
			cls_defs = docstring + f'def __call__({", ".join(arg_exprs)}) -> {result}: ...'
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

		call_arg_type_expr = type_expr
		if ct.kind == 'pointer' and ct.item.kind == 'primitive' and \
			(base_ptype := PRIMITIVES[ct.item.cname].expr) in {'str', 'bytes'}:
			call_arg_type_expr = f'Union[{type_expr}, {base_ptype}]'

		for typedef in typedefs:
			types_defs.append(gen_type_alias(sanitize_typedef_name(typedef), type_expr))

		types_defs_2 = [ (lambda x: lambda: x)(x) if isinstance(x, str) else x for x in types_defs ]
		return type_expr, call_arg_type_expr, types_defs_2

	type_codegen_data = keydefaultdict(process_type)
	for cname in ctypes:
		if ctypes[cname][0].kind == 'void':
			assert cname == 'void' # special case: we'll handle it where it appears
			continue
		types_defs.extend( x() for x in type_codegen_data[cname][2] )

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
			type_expr = f'Literal[{cglobal.value}]'
			docstring = ''
			if cglobal.kind == 'enum':
				type_expr = type_exprs[cname := enum_elements[name].cname]
				docstring = '\n' + fmt_docstr(cname + f' (value {cglobal.value})')
			lib_cls_defs.append(name + ': ' + type_expr + ' = ' + str(cglobal.value) + docstring)
		elif cglobal.kind == 'function' or cglobal.kind == 'python_function' or cglobal.kind == 'constant':
			type_expr = type_exprs[cglobal.type.cname]
			stmt = fmt_var(cglobal.type, name, function=cglobal.kind != "constant")
			docstring = fmt_docstr(f'{cglobal.kind}:\n{fmt_c_block(stmt)}')
			lib_cls_defs.append('@property\n' f'def {name}(self) -> {type_expr}:\n' + indent(docstring + '\n' '...'))
		elif cglobal.kind == 'variable':
			type_expr = type_exprs[cglobal.type.cname]
			stmt = fmt_var(cglobal.type, name)
			docstring = fmt_docstr(f'{cglobal.kind}:\n{fmt_c_block(stmt)}')
			lib_cls_defs.append(name + ': ' + type_expr + ' = ...' '\n' + docstring)
		else:
			assert_never(cglobal)

	# generate the other APIs

	ffi_defs = [FFI_DEFS.replace('\t', _indent_prefix)]

	def def_overloaded(name: str, *overloads: tuple[str, str] | tuple[str, str, str]):
		''' declares an overloaded function, given a series of ([generic params], args, result) tuples '''
		if not overloads: return
		fdef = '\n'.join('@overload\n' f'def {name}{f"[{o[0]}]" if len(o) == 3 else ""}({o[-2]}) -> {o[-1]}: ...' for o in overloads)
		ffi_defs.append(fdef)

	def ctype_names(ct: CType) -> list[str]:
		typedefs = [ bref[1] for bref in ctypes[ct.cname][1] if bref[0] == 'typedef' ]
		typedefs += [ bref[1] + ' *' for bref in ctypes[ct.cname][1] if bref[0] == 'typedef_ptr' ]
		return [ct.cname, *typedefs]

	def initializer_expr(ct: CType):
		if ct.kind != 'pointer' and ct.kind != 'array':
			return
		if type_size[ct.item.cname] == None:
			return
		item_init = type_exprs[ct.item.cname]
		if ct.kind == 'pointer':
			return f'{item_init} = ...'
		init = f'tuple[{item_init}, ...]'
		if ct.item.cname in {'char', 'signed char', 'unsigned char', 'uint8_t', 'int8_t'}:
			init = f'Union[bytes, {init}]'
		if ct.item.cname in {'wchar_t', 'char16_t', 'char32_t'}:
			init = f'Union[str, {init}]'
		if ct.length != None:
			if ct.length <= 5:
				return f'tuple[{", ".join([item_init] * ct.length)}] = ...'
			return f'{init} = ...'
		return f'Union[int, {init}]'

	def_overloaded('new',
		*( (f'self, ctype: {gen_literal(*ctype_names(ct))}, init: {init}, /', type_exprs[ct.cname])
			for ct, _ in ctypes.values() if (init := initializer_expr(ct)) != None ),
	)

	def callable_expr(ct: CType):
		if ct.kind != 'function' or ct.ellipsis:
			return
		result_type = 'None' if ct.result.kind == 'void' else type_exprs[ct.result.cname]
		arg_types = [ type_exprs[arg.cname] for arg in ct.args ]
		return f'Callable[[{", ".join(arg_types)}], {result_type}]'

	def_overloaded('def_extern',
		*( (f'self, name: {gen_literal(name)}, error: Any = ..., onerror: ErrorCallback = ...', f'Callable[[{cexpr}], {cexpr}]')
			for name, cg in cglobals.items() if cg.kind == 'python_function' and (cexpr := callable_expr(cg.type)) )
	)

	def_overloaded('callback',
		*( (f'self, ctype: {gen_literal(*ctype_names(ct))}, error: Any = ..., onerror: ErrorCallback = ...',
	  		f'Callable[[{cexpr}], {type_exprs[ct.cname]}]')
			for ct, _ in ctypes.values() if (cexpr := callable_expr(ct)) != None )
	)

	integral_primitives = [name for name, p in PRIMITIVES.items() if p.expr in {'int', 'bool'} or 'char' in name]
	cast_overloads = [
		# FIXME: for now only primitives, enums and pointers are supported
		('IntPrimitive', [*integral_primitives, 'bool'],
			['int', 'IntPrimitive', 'bool', 'float', 'FloatPrimitive', 'PointerBase[object]']),
		('FloatPrimitive', ["float", "double", "long double"],
			['int', 'IntPrimitive', 'bool', 'float', 'FloatPrimitive']),
		('ComplexPrimitive', ['float _Complex', 'double _Complex', '_cffi_float_complex_t', '_cffi_double_complex_t'],
			['complex', 'ComplexPrimitive', 'int', 'IntPrimitive', 'bool', 'float', 'FloatPrimitive']),
		('EnumCData', [name for name, (ct, _) in ctypes.items() if ct.kind == 'enum'],
			['int', 'IntPrimitive', 'bool']),
		*( (type_exprs[ct.cname], [ct.cname], ['int', 'IntPrimitive', 'PointerBase[object]'])
			for ct, _ in ctypes.values() if ct.kind == 'pointer' or ct.kind == 'function' ),
	]
	def expand_cnames(cnames: list[str]):
		return ( x for cname in cnames for x in (ctype_names(ctypes[cname][0]) if cname in ctypes else [cname]) )
	def_overloaded('cast',
		*( (f'self, ctype: {gen_literal(*expand_cnames(dst_types))}, value: Union[{", ".join(src)}], /', dst) for dst, dst_types, src in cast_overloads )
	)

	composites = [ ct for ct, _ in ctypes.values() if ct.kind == 'struct' or ct.kind == 'union' ]
	def_overloaded('addressof',
		(f'T', 'self, cdata: Pointer[T], index: int, /', 'Pointer[T]'),
		# presumably only sized cdata objects can exist in the wild, so this is guaranteed
		# to return a pointer to sized type and we don't have to restrict it
		(f'T: {cdata_type}', 'self, cdata: T, /', 'Pointer[T]'),
		# specialized signatures for struct/union fields
		*( (f'self, cdata: {type_exprs[ct.cname]}, field: Literal[{name!r}], /',
	  		('Pointer' if type_size[ct.cname] != None else 'PointerBase') + '[' + type_exprs[field.type.cname] + ']')
			for ct in composites for name, field in ct.fields or [] )
	)

	def_overloaded('string',
		# FIXME: this is a bit of a hack. ffi.string() works with pointers to char, returning a bytes.
		# but it also works with pointers to unsigned char or signed char. these are autoconverted to
		# `int`, so we currently define them as type aliases to `int`... and allowing `Pointer[int]`
		# here would accept any integer, of any width, which is not good... for now, require the user
		# to cast e.g. `unsigned char *` to `char *` first
		('self, cdata: Pointer[bytes], maxlength: int = -1', 'bytes'),
		('self, cdata: Pointer[str], maxlength: int = -1', 'str'),
		# not supporting the char/wchar cdata input case -- again, we have no way to detect that specifically
		('self, cdata: EnumCData', 'str') # cffi allows & ignores maxlength in this case, but passing it is a probable bug so let's forbid it
	)
	def_overloaded('unpack',
		('self, cdata: Pointer[bytes], length: int', 'bytes'), # same hack as above applies here
		('self, cdata: Pointer[str], length: int', 'str'),
		('T', 'self, cdata: Pointer[T], length: int', 'list[T]'),
	)

	types_ns_out = f'class {types_ns_name}:\n{indent("\n\n".join(types_defs or ["pass"]))}'
	lib_cls_out = f'class {lib_cls_name}:\n{indent("\n\n".join(lib_cls_defs or ["pass"]))}'
	ffi_cls_out = f'class {ffi_cls_name}(_cffi_backend.FFI):\n{indent("\n\n".join(ffi_defs))}'
	preamble = PREAMBLE.replace('\t', _indent_prefix)
	return f'{preamble}\n\n{types_ns_out}\n\n{lib_cls_out}\n\n{ffi_cls_out}'
