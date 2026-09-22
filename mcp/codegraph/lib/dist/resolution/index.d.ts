/**
 * Reference Resolution Orchestrator
 *
 * Coordinates all reference resolution strategies.
 */
import { UnresolvedReference, Edge } from '../types';
import { QueryBuilder } from '../db/queries';
import { UnresolvedRef, ResolvedRef, ResolutionResult } from './types';
export * from './types';
/**
 * Reference Resolver
 *
 * Orchestrates reference resolution using multiple strategies.
 */
export declare class ReferenceResolver {
    private projectRoot;
    private queries;
    private context;
    private frameworks;
    private deferredChainRefs;
    private deferredThisMemberRefs;
    private razorUsingsCache;
    private nodeCache;
    private fileCache;
    private importMappingCache;
    private reExportCache;
    private nameCache;
    private lowerNameCache;
    private qualifiedNameCache;
    private knownNames;
    private knownFiles;
    private cachesWarmed;
    private projectAliases;
    private goModule;
    private workspacePackages;
    constructor(projectRoot: string, queries: QueryBuilder);
    /**
     * Initialize the resolver (detect frameworks, etc.)
     */
    initialize(): void;
    /**
     * Run each framework resolver's cross-file finalization pass and persist
     * the returned node updates. Idempotent — safe to call after every indexAll
     * and every incremental sync. Returns the number of nodes updated.
     *
     * Caches are cleared before/after so the post-extract pass sees fresh DB
     * state and downstream queries see the updated names.
     */
    runPostExtract(): number;
    /**
     * Pre-build lightweight caches for resolution.
     * Node lookups are now handled by indexed SQLite queries instead of
     * loading all nodes into memory (which caused OOM on large codebases).
     * We cache the set of known symbol names for fast pre-filtering.
     */
    warmCaches(): void;
    /**
     * Clear internal caches
     */
    clearCaches(): void;
    /**
     * Create the resolution context
     */
    private createContext;
    /**
     * Resolve all unresolved references
     */
    resolveAll(unresolvedRefs: UnresolvedReference[], onProgress?: (current: number, total: number) => void): ResolutionResult;
    /**
     * Check if a reference name has any possible match in the codebase.
     * Uses the pre-built knownNames set to skip expensive resolution
     * for names that definitely don't exist as symbols.
     */
    private hasAnyPossibleMatch;
    /**
     * Does `ref.referenceName` match an import declared in its containing
     * file? Used as a pre-filter escape so re-export chain resolution
     * still gets a chance when the name has no project-wide declaration.
     */
    private matchesAnyImport;
    /**
     * Resolve a single reference
     */
    resolveOne(ref: UnresolvedRef): ResolvedRef | null;
    /**
     * Create edges from resolved references
     */
    createEdges(resolved: ResolvedRef[]): Edge[];
    /**
     * Resolve and persist edges to database
     */
    resolveAndPersist(unresolvedRefs: UnresolvedReference[], onProgress?: (current: number, total: number) => void): ResolutionResult;
    /**
     * Second resolution pass for chained static-factory / fluent calls whose
     * chained method is defined on a SUPERTYPE the receiver's type conforms to —
     * a protocol-extension / inherited / default-interface method (#750). The
     * first pass can't resolve these because `implements`/`extends` edges aren't
     * built yet; this runs AFTER edges are persisted, so `context.getSupertypes`
     * (and the conformance fallback in resolveMethodOnType) can walk them.
     *
     * Operates only on the leftover unresolved refs that have the `inner().method`
     * chain shape, for the dotted-chain languages — a small set — and is idempotent
     * (re-resolving an already-resolved ref is a no-op since it's been deleted).
     * Returns the number of newly-created edges.
     */
    resolveChainedCallsViaConformance(): number;
    /**
     * Resolve and persist in batches to keep memory bounded.
     * Processes unresolved references in chunks, persisting edges and cleaning
     * up resolved refs after each batch to avoid accumulating large arrays.
     */
    resolveAndPersistBatched(onProgress?: (current: number, total: number) => void, batchSize?: number): Promise<ResolutionResult>;
    /**
     * Get detected frameworks
     */
    getDetectedFrameworks(): string[];
    /**
     * Check if reference is to a built-in or external symbol
     */
    private isBuiltInOrExternal;
    /**
     * Get file path from node ID
     */
    private getFilePathFromNodeId;
    /**
     * Get language from node ID
     */
    private getLanguageFromNodeId;
    /**
     * Drop an import/name-strategy resolution that crosses a language family.
     * Two regimes (mirrors `applyLanguageGate`'s candidate filter):
     *  - `references` (type usage): STRICT — a `Type.member` static read names a
     *    same-family type, never a coincidentally same-named symbol in another
     *    language. Drops any non-same-family target.
     *  - `imports` (import binding / `#include`): both-known — a C++ `#include
     *    "X.h"` must not resolve to a same-named ObjC header on another platform
     *    (basename collision), but a singleton-family / SFC language (`vue` →
     *    `.ts`) importing across is left alone.
     * Applies to the import (strategy 2) + name-match (strategy 3) results.
     */
    /**
     * Collect the `@using` namespaces in scope for a `.razor`/`.cshtml` file: its
     * own `@using` directives plus every `_Imports.razor` from the file's folder up
     * to the project root (Razor `_Imports` cascade). Cached per file.
     */
    private getRazorUsings;
    /**
     * Resolve a Razor/Blazor simple type ref through the file's `@using`
     * namespaces: `CatalogBrand` + `@using BlazorShared.Models` → the node whose
     * qualified name is `BlazorShared.Models::CatalogBrand`. Only resolves when the
     * `@using` set yields exactly ONE type (otherwise it stays ambiguous and falls
     * through to name-matching).
     */
    private resolveRazorUsing;
    /**
     * Resolve a `this.<member>` function-as-value reference (#756/#808) to the
     * ENCLOSING CLASS's own member — never a same-named symbol elsewhere. The
     * registration idiom (`btn.on('click', this.handleClick)`) names a member
     * of the class being defined, so the only valid target shares the
     * from-symbol's qualified-name scope. Function/method targets only — a
     * property (a data field, post-#808 classification) yields no edge — same
     * file required, no fallback of any kind.
     */
    private resolveThisMemberFnRef;
    /**
     * Second pass for `this.<member>` refs whose member wasn't on the enclosing
     * class itself (#808): once implements/extends edges exist, walk the
     * class's supertypes (transitively, depth-capped) and resolve the member on
     * the nearest one that declares it — `this.handleSubmit` registered in a
     * subclass resolves to `FormBase::handleSubmit`. Validated targets only
     * (function/method kind, same language family); no match → no edge.
     * Mirrors resolveChainedCallsViaConformance's lifecycle. Returns the number
     * of newly-created edges.
     */
    resolveDeferredThisMemberRefs(): number;
    private gateLanguage;
    /**
     * Drop a FRAMEWORK-strategy resolution that crosses two *known* language
     * families for a type-usage (`references`) or import-binding (`imports`)
     * edge. The framework strategy is intentionally ungated for cross-language
     * bridges, but those legitimate bridges are either `calls` edges (RN/Expo
     * JS → native) or config↔code edges whose config side (`yaml`/`blade`/…) is
     * not a known programming-language family. A `references`/`imports` edge
     * between two *known* families is always a coincidental name collision — the
     * React/Svelte/Vue PascalCase component resolvers name-match `getNodesByName`
     * without a language check, so a TS `<TestRunner>` ref happily matched a
     * Kotlin `class TestRunner`. Gating only the both-known-cross-family case
     * lets config bridges and `calls` bridges through untouched.
     */
    private gateFrameworkLanguage;
}
/**
 * Create a reference resolver instance
 */
export declare function createResolver(projectRoot: string, queries: QueryBuilder): ReferenceResolver;
//# sourceMappingURL=index.d.ts.map