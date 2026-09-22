/**
 * Tree-sitter Parser Wrapper
 *
 * Handles parsing source code and extracting structural information.
 */
import { Language, ExtractionResult } from '../types';
export { generateNodeId } from './tree-sitter-helpers';
/**
 * TreeSitterExtractor - Main extraction class
 */
export declare class TreeSitterExtractor {
    private filePath;
    private language;
    private source;
    private tree;
    private nodes;
    private edges;
    private unresolvedReferences;
    private errors;
    private extractor;
    private nodeStack;
    private methodIndex;
    private fnRefSpec;
    private fnRefCandidates;
    constructor(filePath: string, source: string, language?: Language);
    /**
     * Parse and extract from the source code
     */
    extract(): ExtractionResult;
    /**
     * Function-as-value capture (#756): if this node is one of the language's
     * value-position containers (call arguments, assignment RHS, struct/object
     * initializer, array/table literal), collect candidate function names from
     * it. Candidates are gated & flushed at end-of-file (flushFnRefCandidates).
     */
    private maybeCaptureFnRefs;
    /**
     * Candidates-only scan of a subtree the main walkers won't traverse
     * (top-level variable initializers). No extraction side effects. Halts at
     * nested function definitions: their bodies are walked — and their
     * candidates attributed — by extractFunction's own body walk.
     */
    private scanFnRefSubtree;
    /**
     * Gate captured function-as-value candidates and push survivors as
     * `function_ref` unresolved references.
     *
     * The gate bounds volume and protects precision: a candidate survives only
     * if its name matches a function/method DEFINED IN THIS FILE or a name this
     * file imports/references. Everything else (locals, params, fields passed
     * as arguments) is dropped before it ever reaches the database. Resolution
     * then matches survivors against function/method nodes only
     * (matchFunctionRef) and emits `references` edges — which callers/impact
     * already traverse.
     *
     * Known v1 limit, deliberate: a C/C++ callback registered in a DIFFERENT
     * translation unit than its definition (extern, no symbol imports to match)
     * is not captured. Same-file registration — the dominant C pattern (static
     * callback + same-file ops struct) — is.
     */
    private flushFnRefCandidates;
    /**
     * Visit a node and extract information
     */
    private visitNode;
    /**
     * Create a Node object
     */
    private createNode;
    /**
     * Find first named child whose type is in the given list.
     * Used to locate inner type nodes (e.g. enum_specifier inside a typedef).
     */
    private findChildByTypes;
    /**
     * Find a `packageTypes` child under the root, create a `namespace` node
     * for it, and return its id so the caller can scope top-level
     * declarations underneath. Returns null when no package header is
     * present (script files, .kts without a package).
     */
    private extractFilePackage;
    /**
     * Build qualified name from node stack
     */
    private buildQualifiedName;
    /**
     * Build an ExtractorContext for passing to language-specific visitNode hooks.
     */
    private makeExtractorContext;
    /**
     * Check if the current node stack indicates we are inside a class-like node
     * (class, struct, interface, trait). File nodes do not count as class-like.
     */
    private isInsideClassLikeNode;
    /**
     * Extract a function
     */
    private extractFunction;
    /**
     * Extract a class
     */
    private extractClass;
    /**
     * Extract a method
     */
    private extractMethod;
    /**
     * Extract an interface/protocol/trait
     */
    private extractInterface;
    /**
     * Extract a struct
     */
    private extractStruct;
    /**
     * Extract an enum
     */
    private extractEnum;
    /**
     * Extract enum member names from an enum member node.
     * Handles multi-case declarations (Swift: `case put, delete`) and single-case patterns.
     */
    private extractEnumMembers;
    /**
     * Extract a class property declaration (e.g. C# `public string Name { get; set; }`).
     * Extracts as 'property' kind node inside the owning class.
     */
    private extractProperty;
    /**
     * Extract a class field declaration (e.g. Java field_declaration, C# field_declaration).
     * Extracts each declarator as a 'field' kind node inside the owning class.
     */
    private extractField;
    /**
     * Extract function-valued properties of an object literal as named function
     * nodes (named by their property key). Shared by the two object-of-functions
     * shapes in extractVariable: the object as a direct const value, and the
     * object returned by a store-initializer call. Handles both `key: () => {}` /
     * `key: function() {}` pairs and method shorthand `key() {}`.
     */
    private extractObjectLiteralFunctions;
    /** Property-key text with surrounding quotes stripped (`'foo'` → `foo`). */
    private objectKeyName;
    /**
     * Given a `call_expression` initializer (`create((set, get) => ({...}))`),
     * find the object literal RETURNED by a function argument — descending through
     * nested call_expression arguments so middleware wrappers are unwrapped
     * (`create(persist((set, get) => ({...}), {...}))`, devtools, immer,
     * subscribeWithSelector). Returns null when no such object is found — the
     * common case for ordinary call initializers — so this stays cheap and silent
     * rather than guessing. Keyed purely on AST shape; no library names.
     */
    private findInitializerReturnedObject;
    /**
     * The object literal a function expression returns — either the `=> ({...})`
     * arrow form (a parenthesized_expression wrapping an object) or a
     * `=> { return {...} }` block. Returns null for any other body shape.
     */
    private functionReturnedObject;
    /**
     * Extract a variable declaration (const, let, var, etc.)
     *
     * Extracts top-level and module-level variable declarations.
     * Captures the variable name and first 100 chars of initializer in signature for searchability.
     */
    private extractVariable;
    /**
     * Extract a type alias (e.g. `export type X = ...` in TypeScript).
     * For languages like Go, resolveTypeAliasKind detects when the type_spec
     * wraps a struct or interface definition and creates the correct node kind.
     * Returns true if children should be skipped (struct/interface handled body visiting).
     */
    private extractTypeAlias;
    /**
     * Extract the method specs of a Go `interface_type` body as `method` nodes
     * contained by the interface (e.g. `Marshal`, `Unmarshal` of a `Core`
     * interface). tree-sitter-go names these `method_elem` (newer) or
     * `method_spec` (older). Embedded interfaces (`Reader` inside `ReadWriter`)
     * are `type_identifier`s, not methods, and are left to inheritance extraction.
     */
    private extractGoInterfaceMethods;
    /**
     * Surface the members of a TypeScript `type X = { ... }` (or intersection
     * thereof) as `property` / `method` nodes under the type-alias node. Only
     * walks the immediate object_type / intersection operands so anonymous
     * nested object types inside generic arguments (`Promise<{ ok: true }>`)
     * don't produce phantom members.
     */
    private extractTsTypeAliasMembers;
    /**
     * Surface the string-literal "names" of a TypeScript service/contract
     * registry written as a tuple of generic instantiations:
     *
     *   type MyServiceList = [
     *     Service<'query_apply_record', Req, Resp>,
     *     Service<'apply_confirm', Req, Resp>,
     *   ];
     *
     * Each `Service<'name', …>` tags an entry with a string-literal name that a
     * dynamic factory (`createService<MyServiceList>()`) turns into a callable
     * property (`api.query_apply_record(…)`). Static extraction otherwise never
     * sees that name — it's a type argument, not a declaration — so
     * `codegraph query query_apply_record` returned nothing (issue #634). We emit
     * each name as a `method` node under the type alias (qualifiedName
     * `MyServiceList::query_apply_record`) so it's searchable and resolvable as a
     * symbol. (A call through the proxy, `api.query_apply_record(…)`, still
     * resolves to the imported `api` binding — the receiver's type isn't known —
     * so this fixes discoverability, not the per-method call edge.)
     *
     * Scope is deliberately narrow to avoid noise: only a string literal that is
     * a DIRECT type argument of a `generic_type` that is itself a DIRECT element
     * of a `tuple_type`. This excludes utility types (`Pick`/`Omit`/`Record` are
     * never written as tuples) and string args nested deeper
     * (`Service<'a', Pick<U, 'id'>>` yields only `a`, never `id`). Names must be
     * valid identifiers, which also rules out route paths / arbitrary strings.
     */
    private extractTsTupleContractNames;
    /**
     * `foo: () => T` → property_signature whose type_annotation contains a
     * `function_type`. Treat that as a method-shaped contract member, since
     * the call site `obj.foo()` has identical semantics to `bar(): T`.
     */
    private isTsFunctionTypedProperty;
    /**
     * Extract an import
     *
     * Creates an import node with the full import statement stored in signature for searchability.
     * Also creates unresolved references for resolution purposes.
     */
    private extractImport;
    /**
     * Emit one `imports` reference per named/default import binding (TS/JS family),
     * attributed to the file node — so the resolver links each imported symbol to
     * the file that DEFINES it.
     *
     * Importing a symbol IS a dependency, but extraction only emits references for
     * calls, instantiations, type annotations, and inheritance. A symbol that's
     * imported and then only re-exported (`export { X } from './x'`), placed in a
     * registry array (`[expressResolver, …]`), passed as an argument, or used in
     * JSX produced NO cross-file edge at all — so the providing file showed a
     * false "0 dependents" and was invisible to blast-radius / `affected`. The
     * resolver maps the local name (alias-aware) to the provider's definition and
     * creates a cross-file `imports` edge; `getFileDependents` picks it up, while
     * `getImpactRadius` keeps it as a bounded leaf (the importing file node).
     *
     * Namespace imports (`import * as NS`) bind a whole module: `NS.member` calls
     * resolve on their own, but a namespace used ONLY via a value-member read
     * (`NS.SOME_CONST`) would leave no edge — so we also emit the namespace local
     * name, which the resolver links to the module FILE as a dependency backstop.
     */
    private emitImportBindingRefs;
    /**
     * Emit one `imports` reference per re-exported binding of a
     * `export { A, B as C } from './y'` statement, attributed to the file node —
     * so a barrel that re-exports from another module records a dependency on it.
     *
     * Links the SOURCE-side name (`A`, the `name` field — not the local alias
     * `C`), since that is what the source module defines. `export * from './y'`
     * has no named bindings to attribute and `export { default as X }` can't be
     * name-matched, so both are skipped.
     */
    private emitReExportRefs;
    /**
     * Emit one `imports` reference per binding of a Rust `use` declaration —
     * `use crate::m::Item`, `use crate::m::{A, B as C}`, `pub use self::sub::Item`.
     * Emits the FULL path (e.g. `self::sub::Item`, not just `Item`) so the resolver
     * can resolve the module prefix to a file and find the leaf symbol there —
     * disambiguating common-name re-exports (`pub use self::read::read`, where the
     * leaf `read` collides with many same-named symbols). Falls back to name-match
     * on the leaf when the path can't be resolved. `use ...::*` has no leaf binding.
     */
    private emitRustUseBindingRefs;
    /**
     * Emit an `imports` reference for a single PHP `use Foo\Bar\Baz;` (grouped
     * imports `use Foo\{A, B}` are handled where their per-item nodes are created).
     * The reference targets the namespace-qualified `Foo\Bar::Baz` form classes are
     * stored under (see the PHP `namespace` capture), so it resolves to the RIGHT
     * definition — Laravel has many same-named contracts (`Factory`, `Dispatcher`,
     * `Guard`) across namespaces that a bare-name match can't disambiguate.
     */
    private emitPhpUseRefs;
    /**
     * Ruby `require`/`require_relative` → an `imports` ref to the required FILE.
     * `require "sidekiq/fetch"` is load-path-relative (matched by file-path suffix
     * via {@link matchByFilePath}); `require_relative "../foo"` is resolved against
     * this file's directory. Bare gem/stdlib requires (`require "json"`, no slash)
     * are skipped — they're external. The path form (a `/` + `.rb`) makes the ref
     * resolve to the file node, so a file pulled in only by `require` — not by a
     * resolved constant/call — still records a cross-file dependency.
     */
    private emitRubyRequireRefs;
    /** Convert a PHP FQN `Foo\Bar\Baz` to the stored `Foo\Bar::Baz` and emit an `imports` ref. */
    private pushPhpUseRef;
    /**
     * Emit one `imports` reference per name imported in a Python
     * `from module import A, B as C` statement, attributed to the file node — so
     * the resolver links each imported name to the module that DEFINES it.
     *
     * Same recall gap as TS: extraction only emitted references for calls,
     * instantiations, and inheritance, so a name imported and then used in a
     * non-call position (a list/dict literal, a default argument, a decorator
     * target, or simply re-exported through an `__init__.py` barrel) produced no
     * cross-file edge — the providing module showed a false "0 dependents". Links
     * the LOCAL name (alias when present, since that's what the resolver's import
     * mapping keys on); `from module import *` has no names to attribute.
     */
    private emitPyFromImportRefs;
    /**
     * Extract a function call
     */
    private extractCall;
    /**
     * `new Foo(...)` / `Foo::new(...)` / object_creation_expression —
     * emit an `instantiates` reference to the class name. The resolver
     * then links it to the class node, producing the `instantiates`
     * edge that powers "what creates instances of X" queries.
     *
     * Children are still walked so nested calls inside the constructor
     * arguments (`new Foo(bar())`) get their own `calls` references.
     */
    private extractInstantiation;
    /**
     * Static-member / value-read pass. A type/enum/class used only via a member
     * VALUE — `Enum.value`, `Type.CONST`, `Colors.red`, `Foo::BAR` — recorded no
     * edge, because the body walker only handled CALLS (`Type.method()`). So a
     * type referenced only by an enum value or a static field looked like nothing
     * depended on it (the residual frontier across Dart/Java/C#/Swift/Kotlin/PHP).
     * Emit a `references` edge to the capitalized receiver. Gated to languages
     * where types are Capitalized by convention, and skipped when the access is a
     * call's callee (the call extractor already links the method).
     */
    private extractStaticMemberRef;
    private pushStaticMemberRef;
    /**
     * Find a `class_body` child of an `object_creation_expression` — the
     * marker for an anonymous class (`new T() { ... }`). Returns the body
     * node so the caller can walk it as the anon class's members.
     */
    private findAnonymousClassBody;
    /**
     * Extract a Java/C# anonymous class — `new T() { ...members }`. Emits a
     * `class` node named `<T$anon@line>`, an `extends` reference to T (so
     * Phase 5.5 interface-impl can bridge), and walks the body so its
     * `method_declaration` members become method nodes under the anon class.
     *
     * Why this matters: without anon-class extraction, the overrides inside
     * a lambda-returned `new T() { @Override int foo(){...} }` are not nodes,
     * so a call through T.foo (the abstract parent method) has no static
     * target — the agent has to Read the file to find the implementation.
     */
    private extractAnonymousClass;
    /**
     * Scan `declNode` and its preceding siblings (within the parent's
     * named children) for decorator nodes, emitting a `decorates`
     * reference from `decoratedId` to each decorator's function name.
     *
     * Why preceding siblings: in TypeScript, `@Foo class Bar {}` parses
     * as an `export_statement` (or top-level wrapper) with the
     * `decorator` as a child *before* the `class_declaration` — so the
     * decorator isn't a child of the class itself. For methods/
     * properties, the decorator IS a direct child of the declaration,
     * so we also scan declNode.namedChildren.
     *
     * Idempotent across grammars: if neither location yields decorators
     * (most non-decorator-using languages), the function is a no-op.
     */
    private extractDecoratorsFor;
    /**
     * Visit function body and extract calls (and structural nodes).
     *
     * In addition to call expressions, this also detects class/struct/enum
     * definitions inside function bodies. This handles two cases:
     *   1. Local class/struct/enum definitions (valid in C++, Java, etc.)
     *   2. C++ macro misparsing — macros like NLOHMANN_JSON_NAMESPACE_BEGIN cause
     *      tree-sitter to interpret the namespace block as a function_definition,
     *      hiding real class/struct/enum nodes inside the "function body".
     */
    /**
     * Rocket route-registration macros — `routes![a::b::handler, c::d::other]`
     * and `catchers![not_found]`. Tree-sitter leaves a macro body as a flat
     * `token_tree` of raw tokens (`identifier`, `::`, `,`), so the handler paths
     * are never seen as references and each handler fn looks like it has no caller
     * — it's mounted by Rocket at runtime, not called by in-repo code, so its file
     * shows 0 dependents. Walk the token tree, reconstruct each comma-separated
     * path, and emit a `references` edge; the Rust path resolver
     * (`resolveRustPathReference`) then links it to the handler fn. The handler
     * names are explicit in source, so this is precise static extraction, not a
     * heuristic — no false edges (resolution still validates each path).
     */
    private extractRustRouteMacro;
    private visitFunctionBody;
    /**
     * Extract inheritance relationships
     */
    private extractInheritance;
    /**
     * Rust `impl Trait for Type` — creates an implements edge from Type to Trait.
     * For plain `impl Type { ... }` (no trait), no inheritance edge is needed.
     */
    private extractRustImplItem;
    /**
     * Find a previously-extracted node by name (used for back-references like impl blocks)
     */
    private findNodeByName;
    /**
     * Languages that support type annotations (TypeScript, etc.)
     */
    private readonly TYPE_ANNOTATION_LANGUAGES;
    /**
     * PHP pseudo-types and `self`/`static`/`parent` that aren't project symbols.
     * (Scalar primitives parse as `primitive_type` and are skipped structurally.)
     */
    private readonly PHP_PSEUDO_TYPES;
    /**
     * Built-in/primitive type names that shouldn't create references
     */
    private readonly BUILTIN_TYPES;
    /**
     * Extract type references from type annotations on a function/method/field node.
     * Creates 'references' edges for parameter types, return types, and field types.
     */
    private extractTypeAnnotations;
    /**
     * Extract C# type references from a node that owns a type position —
     * a method/constructor declaration, a property declaration, or a
     * field declaration (which wraps `variable_declaration → type`).
     *
     * Walks ONLY into known type fields, so parameter names like
     * `request` in `Build(UserDto request)` are never mis-emitted as
     * type references. Once inside a type subtree, `walkCsharpTypePosition`
     * recognizes C#'s actual type-leaf node kinds (`identifier`,
     * `qualified_name`, `generic_name`, `array_type`, `nullable_type`,
     * `tuple_type`, …) — none of which are `type_identifier`. Closes #381.
     */
    private extractCsharpTypeRefs;
    /**
     * Record the dependencies declared by a C# PRIMARY CONSTRUCTOR
     * (`class Svc(IRepo repo, [FromKeyedServices("k")] ICache cache) { … }`,
     * C# 12+). The parameter list hangs off the class/struct/record declaration
     * as an unnamed-field `parameter_list` child (not the `parameters` field a
     * method uses), so it's found by node type. Each parameter's declared type
     * becomes a `references` edge from the owning type — these are exactly the
     * services a DI-registered type depends on, so impact/blast-radius and
     * "who depends on this contract" now see them. No-op when there's no primary
     * constructor. (#237)
     */
    private extractCsharpPrimaryCtorParamRefs;
    /**
     * Walk a C# subtree that is KNOWN to be in a type position
     * (return type, parameter type, property type, field type, generic
     * argument). Identifiers here are type names, not parameter names.
     */
    private walkCsharpTypePosition;
    /**
     * Extract PHP type references from a method/function/property declaration.
     * Walks ONLY type positions: each parameter's type child (inside
     * `formal_parameters`), the return type, and a property's type — all
     * `named_type` / `optional_type` / `union_type` / … direct children. Parameter
     * and property NAMES are `variable_name` (`$x`), never type nodes, so they
     * can't be mis-emitted.
     */
    private extractPhpTypeRefs;
    /** Walk a PHP subtree KNOWN to be in a type position; emit class/interface refs. */
    private walkPhpTypePosition;
    /**
     * Extract type references from a variable's type annotation.
     */
    private extractVariableTypeAnnotation;
    /**
     * Recursively walk a subtree and extract all type_identifier references.
     * Handles unions, intersections, generics, arrays, etc.
     */
    private extractTypeRefsFromSubtree;
    /**
     * Handle Pascal-specific AST structures.
     * Returns true if the node was fully handled and children should be skipped.
     */
    private visitPascalNode;
    /**
     * Extract a Pascal declType node (class, interface, enum, or type alias)
     */
    private extractPascalDeclType;
    /**
     * Extract Pascal uses clause into individual import nodes
     */
    private extractPascalUses;
    /**
     * Extract a Pascal constant declaration
     */
    private extractPascalConst;
    /**
     * Extract Pascal inheritance (extends/implements) from declClass typeref children
     */
    private extractPascalInheritance;
    /**
     * Extract calls and resolve method context from a Pascal defProc (implementation body).
     * Does not create a new node — the declaration was already captured from the interface section.
     */
    private extractPascalDefProc;
    /**
     * Extract function calls from a Pascal expression
     */
    private extractPascalCall;
    /**
     * Extract a PAREN-LESS Pascal method/procedure call (`Obj.Method;`,
     * `TFoo.GetInstance.DoIt;`). Pascal lets a no-arg method drop its parens, so it
     * parses as a bare `exprDot` (not an `exprCall`). A bare `exprDot` is
     * syntactically identical to a field/property access, so this is only ever
     * called for a STATEMENT-level exprDot (caller-gated): a bare `Obj.Field;`
     * statement is a no-op, so a statement-level dot expression is a call. (An
     * exprDot in assignment LHS/RHS or a condition is left alone — there it really
     * can be a field/property read.)
     */
    private extractPascalParenlessCall;
    /**
     * Recursively visit a Pascal block/statement tree for call expressions
     */
    private visitPascalBlock;
}
/**
 * Extract nodes and edges from source code.
 *
 * If `frameworkNames` is provided, framework-specific extractors matching
 * those names and the file's language are run after the tree-sitter pass.
 * Their nodes/references/errors are merged into the returned result.
 */
export declare function extractFromSource(filePath: string, source: string, language?: Language, frameworkNames?: string[]): ExtractionResult;
//# sourceMappingURL=tree-sitter.d.ts.map