import type { LanguageExtractor } from '../tree-sitter-types';
/**
 * Normalize a C++ return type to the bare class name a method could be called
 * on. Unwraps smart-pointer / optional wrappers to their element type
 * (`std::unique_ptr<Widget>` → `Widget`) so a factory's `->method()` resolves on
 * the pointee. Strips cv-qualifiers, `&`/`*`, namespace qualifiers, and other
 * template args. Returns undefined for primitives / void / `auto` / empty.
 */
export declare function normalizeCppReturnType(raw: string): string | undefined;
export declare const cExtractor: LanguageExtractor;
export declare const cppExtractor: LanguageExtractor;
//# sourceMappingURL=c-cpp.d.ts.map