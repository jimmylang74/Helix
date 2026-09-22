/**
 * Name Matcher
 *
 * Handles symbol name matching for reference resolution.
 */
import { UnresolvedRef, ResolvedRef, ResolutionContext } from './types';
/**
 * Try to resolve a path-like reference (e.g., "snippets/drawer-menu.liquid")
 * by matching the filename against file nodes.
 */
export declare function matchByFilePath(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
export declare function sameLanguageFamily(a: string, b: string): boolean;
/**
 * True when `lang` belongs to a known multi-language family (jvm/apple/web/c).
 * Languages not listed (php, python, go, ruby, rust, dart, …) and config
 * formats (yaml/xml/blade) form their own singleton families and return
 * `false` — used to leave config↔code framework bridges (whose config side is
 * never a known programming-language family) out of the cross-family gate.
 */
export declare function isKnownLanguageFamily(lang: string): boolean;
/**
 * True when `a` and `b` are two DIFFERENT *known* language families — the
 * signature of a coincidental cross-language name collision (a TS `import
 * React` matching a Swift `import React`, a C++ `#include "X.h"` matching a
 * same-named ObjC header on another platform). The both-*known* test is
 * deliberately weaker than {@link sameLanguageFamily}'s negation: a
 * single-file-component language that carries its own tag (`vue`/`svelte`)
 * importing a `.ts` module, or any singleton-family language (php/go/ruby/…),
 * returns `false` here and is left alone.
 */
export declare function crossesKnownFamily(a: string, b: string): boolean;
/**
 * Resolve a function-as-value reference (#756) — a function name used as a
 * callback/function-pointer value (`register(handler)`, `o->cb = handler`,
 * `{ .cb = handler }`, `signal(SIGINT, handler)`). The ONLY strategy allowed
 * for `function_ref` refs: exact name, function/method targets only, same
 * language family, same-file first, and cross-file only when the match is
 * UNIQUE. No fuzzy fallback, no qualified-name walking — a wrong callback
 * edge is worse than none.
 */
export declare function matchFunctionRef(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Try to resolve a reference by exact name match
 */
export declare function matchByExactName(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Try to resolve by qualified name
 */
export declare function matchByQualifiedName(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Resolve a C++ chained call whose receiver is itself a call — encoded by the
 * extractor as `<innerCallee>().<method>` (#645). The receiver's type is what
 * the inner call returns; the outer method is then resolved and VALIDATED on it
 * (resolveMethodOnType requires `cls::method` to exist), so a wrong inference
 * produces no edge rather than a wrong one.
 */
export declare function matchCppCallChain(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Resolve a `::`-scoped factory chain whose receiver is a scoped/static call —
 * PHP `Cls::for($x)->method()` (#608, the per-credential Laravel client idiom) or
 * Rust `Foo::new().bar()` (an associated-function call) — both encoded by the
 * extractor as `Cls::factory().method`. The receiver's type is what `Cls::factory`
 * returns: a `self` marker (PHP `: self`/`: static`, Rust `-> Self`) resolves to
 * the factory's own type, a concrete return type to that type. The outer method is
 * then resolved and VALIDATED on it (resolveMethodOnType requires the method to
 * exist on the type or a supertype it conforms to), so a wrong inference yields no
 * edge rather than a wrong one. Shared by the `::`-receiver languages (PHP, Rust).
 */
export declare function matchScopedCallChain(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Resolve a dotted chained call whose receiver is a static factory / fluent call —
 * `Foo.getInstance().bar()`, encoded by the extractor as `Foo.getInstance().bar`
 * (#645/#608 mechanism). The receiver's type is what `Foo.getInstance` returns
 * (its declared return type); the outer method is then resolved and VALIDATED on
 * it (resolveMethodOnType requires `Type::method` to exist), so a wrong inference
 * yields no edge rather than a wrong one (e.g. a same-named `bar()` on an
 * unrelated class is never matched). Shared by the dot-notation languages
 * (Java, Kotlin, C#, Swift) — same receiver shape, same `Class::method` qualified names.
 */
export declare function matchDottedCallChain(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Try to resolve by method name on a class/object
 */
export declare function matchMethodCall(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Fuzzy match - last resort with lower confidence
 */
export declare function matchFuzzy(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
/**
 * Match all strategies in order of confidence
 */
export declare function matchReference(ref: UnresolvedRef, context: ResolutionContext): ResolvedRef | null;
//# sourceMappingURL=name-matcher.d.ts.map