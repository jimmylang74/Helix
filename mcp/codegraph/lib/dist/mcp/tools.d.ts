/**
 * MCP Tool Definitions
 *
 * Defines the tools exposed by the CodeGraph MCP server.
 */
import type CodeGraph from '../index';
import type { PendingFile } from '../sync';
/**
 * An expected, recoverable "codegraph can't serve this" condition — most
 * importantly a project with no index. The dispatch catch converts these to
 * SUCCESS-shaped responses (guidance text, NO isError): an `isError: true`
 * early in a session teaches the agent the toolset is broken and it stops
 * calling codegraph entirely (observed repeatedly), which is exactly wrong
 * for conditions the agent can simply work around (use built-in tools for
 * that codebase / pass projectPath). isError is reserved for "stop trying"
 * cases: security refusals ({@link PathRefusalError}) and genuine
 * malfunctions.
 */
export declare class NotIndexedError extends Error {
}
/**
 * A security refusal (sensitive system path). Stays `isError: true` WITHOUT
 * retry guidance — abandoning this path is the desired agent reaction.
 */
export declare class PathRefusalError extends Error {
}
/**
 * Calculate the recommended number of codegraph_explore calls based on project size.
 * Larger codebases need more exploration calls to cover their surface area,
 * but smaller ones should use fewer to avoid unnecessary overhead.
 */
export declare function getExploreBudget(fileCount: number): number;
/**
 * Adaptive output budget for `codegraph_explore`, scaled to project size.
 *
 * Smaller codebases get a tighter total cap, fewer default files, smaller
 * per-file cap, and tighter clustering — so a focused query on a 100-file
 * project doesn't dump a whole file's worth of source into the agent's
 * context. Larger codebases keep the generous defaults because the
 * agent's native discovery cost (grep + find + many Reads) genuinely
 * dwarfs a fat explore call at that scale.
 *
 * Meta-text (relationships map, "additional relevant files" list,
 * completeness signal, budget note) is gated off for tiny projects
 * where one rich call is the whole story and the extra prose is just
 * overhead.
 *
 * Tier breakpoints mirror `getExploreBudget` so a project sits in the
 * same tier across both knobs.
 */
export interface ExploreOutputBudget {
    /** Hard cap on total output characters. */
    maxOutputChars: number;
    /** Default `maxFiles` when the caller didn't specify one. */
    defaultMaxFiles: number;
    /** Cap on contiguous source returned per file (across all its clusters). */
    maxCharsPerFile: number;
    /** Cluster gap threshold in lines — tighter clustering on small projects. */
    gapThreshold: number;
    /** Max symbols listed in the per-file header (`#### path — sym(kind), ...`). */
    maxSymbolsInFileHeader: number;
    /** Max edges shown per relationship kind in the Relationships section. */
    maxEdgesPerRelationshipKind: number;
    /** Include the "Relationships" section. */
    includeRelationships: boolean;
    /** Include the "Additional relevant files (not shown)" trailing list. */
    includeAdditionalFiles: boolean;
    /** Include the "Complete source code is included above…" reminder. */
    includeCompletenessSignal: boolean;
    /** Include the explore-budget reminder at the end. */
    includeBudgetNote: boolean;
    /**
     * Hard-drop test/spec/icon/i18n files from the relevant-file set unless
     * the query itself mentions tests. Today they're only deprioritized in
     * the sort, which on tiny repos still lets one slip into the top N (e.g.
     * cobra's `command_test.go` displaced `args.go` and contributed ~10KB of
     * pure noise to "How does cobra parse commands?"). Off by default; on
     * for the very-tiny tier where one slip dominates the budget.
     */
    excludeLowValueFiles: boolean;
}
export declare function getExploreOutputBudget(fileCount: number): ExploreOutputBudget;
/**
 * Per-file staleness banner emitted at the top of a tool response when the
 * file watcher has pending events for files referenced by the response.
 * The agent uses this to fall back to Read for those specific files
 * without waiting for the debounced sync (issue #403).
 */
export declare function formatStaleBanner(stale: PendingFile[]): string;
/**
 * Compact footer listing pending files that are NOT referenced in this
 * response. Gives the agent a complete project-wide freshness picture
 * without bloating the main banner.
 */
export declare function formatStaleFooter(stale: PendingFile[]): string;
/**
 * MCP Tool definition
 */
export interface ToolDefinition {
    name: string;
    description: string;
    inputSchema: {
        type: 'object';
        properties: Record<string, PropertySchema>;
        required?: string[];
    };
}
interface PropertySchema {
    type: string;
    description: string;
    enum?: string[];
    default?: unknown;
}
/**
 * Tool execution result
 */
export interface ToolResult {
    content: Array<{
        type: 'text';
        text: string;
    }>;
    isError?: boolean;
}
/**
 * All CodeGraph MCP tools
 *
 * Designed for minimal context usage - use codegraph_explore as the primary tool
 * (one call usually answers the whole question), and only use other tools for
 * targeted follow-up queries.
 *
 * All tools support cross-project queries via the optional `projectPath` parameter.
 */
export declare const tools: ToolDefinition[];
/**
 * Allowlist-filtered tool definitions WITHOUT an engine — the static surface the
 * proxy answers `tools/list` with before any project is open. Mirrors
 * `ToolHandler.getTools()` in the no-CodeGraph case (the dynamic per-repo budget
 * note in a description only adds once `cg` is loaded; the schemas are static).
 */
export declare function getStaticTools(): ToolDefinition[];
/**
 * Tool handler that executes tools against a CodeGraph instance
 *
 * Supports cross-project queries via the projectPath parameter.
 * Other projects are opened on-demand and cached for performance.
 */
export declare class ToolHandler {
    private cg;
    private projectCache;
    private defaultProjectHint;
    private worktreeMismatchCache;
    private catchUpGate;
    constructor(cg: CodeGraph | null);
    /**
     * Update the default CodeGraph instance (e.g. after lazy initialization)
     */
    setDefaultCodeGraph(cg: CodeGraph): void;
    /**
     * Engine-only: register the catch-up sync promise so the next `execute()`
     * call awaits it before serving. The handler swallows rejections (the
     * engine logs them) so a sync failure never propagates as a tool error;
     * we still want to serve a best-effort result over the same potentially-
     * stale data, which is what would have happened without the gate.
     */
    setCatchUpGate(p: Promise<void> | null): void;
    /**
     * Record the directory the server tried to resolve the default project from.
     * Used only to make the "no default project" error actionable.
     */
    setDefaultProjectHint(searchedPath: string): void;
    /**
     * Whether a default CodeGraph instance is available
     */
    hasDefaultCodeGraph(): boolean;
    /**
     * Optional allowlist of exposed tools, parsed from the CODEGRAPH_MCP_TOOLS
     * env var (comma-separated short names, e.g. "trace,search,node,context").
     * Unset/empty → every tool is exposed. Lets an operator (or an A/B harness)
     * trim the tool surface without rebuilding the client config; the ablated
     * tool is then truly absent from ListTools rather than merely denied on call.
     * Matching is on the short form, so "node" and "codegraph_node" both work.
     */
    private toolAllowlist;
    /** Whether a tool name passes the CODEGRAPH_MCP_TOOLS allowlist (if any). */
    private isToolAllowed;
    /**
     * Get tool definitions with dynamic descriptions based on project size.
     * The codegraph_explore tool description includes a budget recommendation
     * scaled to the number of indexed files. Honors the CODEGRAPH_MCP_TOOLS
     * allowlist so a trimmed surface is reflected in ListTools.
     */
    getTools(): ToolDefinition[];
    /**
     * Get CodeGraph instance for a project
     *
     * If projectPath is provided, opens that project's CodeGraph (cached).
     * Otherwise returns the default CodeGraph instance.
     *
     * Walks up parent directories to find the nearest .codegraph/ folder,
     * similar to how git finds .git/ directories.
     */
    private getCodeGraph;
    /**
     * Close all cached project connections
     */
    closeAll(): void;
    /**
     * Validate that a value is a non-empty string within length bounds.
     *
     * The `maxLength` cap protects against MCP clients that ship huge
     * payloads (10MB+ query strings either by accident or maliciously).
     * Without this, a single oversized input can pin the FTS5 index or
     * exhaust memory before any real work runs.
     */
    private validateString;
    /**
     * Validate an optional path-like string input. Returns the value if
     * valid (or undefined), or a ToolResult with the error.
     */
    private validateOptionalPath;
    /**
     * Cached git worktree/index mismatch for a tool call's effective project.
     *
     * The "effective project" is what the request targets: an explicit
     * `projectPath` arg, else the directory the server resolved its default
     * project from (`defaultProjectHint`), else cwd. Memoized per start path —
     * see `worktreeMismatchCache`. Best-effort: if the project can't be resolved
     * (e.g. nothing initialized yet), it reports "no mismatch" so a tool is never
     * broken by this check.
     */
    private worktreeMismatchFor;
    /**
     * Prefix a successful read-tool result with a compact worktree-mismatch
     * notice when the resolved index belongs to a different git working tree than
     * the caller's (issue #155). Without this, an agent in a nested worktree
     * silently trusts main-branch results. No-op on error results and when there
     * is no mismatch. `codegraph_status` is excluded — it embeds its own verbose
     * warning — so it stays out of this path.
     */
    private withWorktreeNotice;
    /**
     * Annotate a successful read-tool result with per-file staleness — the
     * non-blocking answer to issue #403. The file watcher tracks every event
     * it sees per path; here we intersect "files referenced in this response"
     * against that pending set and prepend a compact banner so the agent can
     * fall back to Read for those *specific* files without waiting for the
     * debounced sync to fire. Other pending files in the project (not
     * referenced by this response) get a small footer so the agent has a
     * complete picture without bloating the banner.
     *
     * Cost when nothing is pending — the common case — is one boolean check.
     * No I/O, no parsing of markdown beyond a per-pending-file substring scan.
     */
    private withStalenessNotice;
    /**
     * Execute a tool by name
     */
    execute(toolName: string, args: Record<string, unknown>): Promise<ToolResult>;
    /**
     * Handle codegraph_search
     */
    private handleSearch;
    /**
     * Group symbol matches into DISTINCT DEFINITIONS — one group per
     * (filePath, qualifiedName), so same-file overloads stay together while
     * unrelated same-named classes across a monorepo's apps (#764: one
     * `UserService` per NestJS app) are kept apart. Optionally narrowed by a
     * `file` path/suffix first.
     */
    private groupDefinitions;
    /** Section heading for one distinct definition in grouped output. */
    private definitionHeading;
    /**
     * Handle codegraph_callers
     */
    private handleCallers;
    /**
     * Handle codegraph_callees
     */
    private handleCallees;
    /**
     * Handle codegraph_impact
     */
    private handleImpact;
    /**
     * Describe a synthesized (dynamic-dispatch) edge for human output: how the
     * callback was wired up — the bridge static parsing can't see. Returns null
     * for ordinary static edges. Used by trace + the node trail so a synthesized
     * hop reads as "registered via onUpdate at App.tsx:3148", not a bare arrow.
     */
    private synthEdgeNote;
    /**
     * Flow-from-named-symbols: an agent's codegraph_explore query is a bag of
     * symbol names that usually spans the flow it's investigating (e.g.
     * "PmsProductController getList PmsProductService list PmsProductServiceImpl").
     * Surface the longest call chain AMONG those named symbols — scoped to what the
     * agent explicitly named, so (unlike a fuzzy relevance set) there's no
     * wrong-feature wandering. Rides synthesized edges, so controller→service-
     * interface→impl shows up. Returns '' if no chain of >=3 nodes exists.
     *
     * Ambiguous tokens (Java `list` → dozens of nodes) are disambiguated by
     * CO-NAMING: the agent names the class too, so we keep only `list` candidates
     * whose qualifiedName contains another named token (`PmsProductServiceImpl::list`),
     * dropping unrelated `OmsOrderService::list`.
     */
    private buildFlowFromNamedSymbols;
    /**
     * Dynamic-boundary surfacing (#687): when the flow among the agent's named
     * symbols does not fully connect, scan the disconnected symbols' bodies for
     * dynamic-dispatch sites (computed member calls, getattr, reflection, typed
     * message buses, runtime-keyed emits) and ANNOUNCE the boundary — the exact
     * site, the form, and (when a key is statically visible) candidate targets —
     * instead of guessing edges. The answer to "how does A reach B" when no
     * static path exists IS the dispatch site: that's where the flow continues
     * at runtime. Query-time, deterministic, zero graph mutation; a fully
     * connected flow never reaches this method.
     */
    private buildDynamicBoundaries;
    /**
     * Shortlist candidate runtime targets for a dispatch key surfaced by
     * {@link buildDynamicBoundaries}. Exact conventional names first (`save` →
     * `onSave`/`handleSave`; `CreateCmd` → `CreateCmdHandler`), then FTS, with a
     * normalized-containment post-filter (FTS camel-splitting is fuzzier than a
     * candidate list should be). Symbols the agent already named sort first and
     * are marked — that's the "you were right, here's the wiring" case.
     */
    private boundaryCandidates;
    /**
     * Compact "blast radius" for the entry symbols of an explore result: who
     * depends on each (callers) and which test files cover it — LOCATIONS ONLY,
     * no source, so the agent knows what to update / re-verify before editing
     * without reaching for a separate impact call. Always-on, but skips symbols
     * that have no dependents (nothing to warn about), and returns '' when none
     * qualify so a leaf-only exploration stays clean.
     */
    private buildBlastRadiusSection;
    /**
     * Graph-connectivity relevance via Random-Walk-with-Restart (personalized
     * PageRank) from the query's matched SEED nodes over the call/reference graph.
     *
     * This is the ranking signal text search (FTS/bm25) CANNOT provide, and it's
     * codegraph's home turf: relevance by STRUCTURE, not words. A file whose
     * symbols are call-connected to the matched cluster accrues walk mass and
     * ranks high; a lone TEXT match — e.g. `LensSwitcher.swift` matched the word
     * "switch" from `switchOrganization`, but calls none of `setUser`/`fetchUser`
     * — gets only its own restart probability and ranks ~0. Immune to the
     * tokenization trap that fools term matching, deterministic, no embeddings.
     *
     * Undirected adjacency (reachability both ways), restart α=0.25 to the seeds,
     * power iteration to convergence. Bounded to the already-relevant subgraph, so
     * it's a few hundred nodes × ~25 iterations — negligible cost.
     */
    private computeGraphRelevance;
    /**
     * Handle codegraph_explore — deep exploration in a single call
     *
     * Strategy: find relevant symbols via graph traversal, group by file,
     * then read contiguous file sections covering all symbols per file.
     * This replaces multiple codegraph_node + Read calls.
     *
     * Output size is adaptive to project file count via
     * `getExploreOutputBudget` — see #185 for why a fixed 35k cap was a
     * tax on small projects while earning its keep on large ones.
     */
    private handleExplore;
    /**
     * Handle codegraph_node
     */
    private handleNode;
    /**
     * FILE READ MODE: resolve `fileArg` (path or basename) to an indexed file and
     * read it like the Read tool — its current on-disk source with line numbers,
     * narrowable with `offset`/`limit` exactly as Read's are — preceded by a
     * one-line blast-radius header (which files depend on it). `symbolsOnly`
     * returns just the structural map (symbols + dependents) instead of source.
     *
     * Parity goal: the numbered source block is byte-for-byte the shape Read
     * returns (`<n>\t<line>`, no padding), so the agent treats it as a Read — only
     * faster (served from the index) and with the blast radius attached. Security:
     * yaml/properties files are summarized by key, never dumped (#383); reads go
     * through validatePathWithinRoot (#527).
     */
    private handleFileView;
    /** Render one symbol: details + (optional) body/outline + its caller/callee trail. */
    private renderNodeSection;
    /**
     * Build the "trail" for a symbol: its direct callees (what it calls) and
     * callers (what calls it), each with file:line — so codegraph_node doubles as
     * the structural Grep→Read→expand primitive: a spot PLUS where to go next.
     * Capped to stay cheap. Walk the graph by calling codegraph_node on a trail
     * entry; no Read needed for covered hops. Empty edges on a non-leaf often mean
     * dynamic dispatch the static graph couldn't resolve — that absence is itself
     * a signal (read that one hop) rather than a dead end.
     */
    private formatTrail;
    /**
     * Handle codegraph_status
     */
    private handleStatus;
    /**
     * Handle codegraph_files - get project file structure from the index
     */
    private handleFiles;
    /**
     * Convert glob pattern to regex
     */
    private globToRegex;
    /**
     * Format files as a flat list
     */
    private formatFilesFlat;
    /**
     * Format files grouped by language
     */
    private formatFilesGrouped;
    /**
     * Format files as a tree structure
     */
    private formatFilesTree;
    /**
     * Find a symbol by name, handling disambiguation when multiple matches exist.
     * Returns the best match and a note about alternatives if any.
     */
    /**
     * Check if a node matches a symbol query.
     *
     * Accepts simple names (`run`) and three flavors of qualifier:
     *   - dotted     `Session.request`         (TS/JS/Python)
     *   - colon-pair `stage_apply::run`        (Rust, C++, Ruby)
     *   - slash      `configurator/stage_apply` (path-ish)
     *
     * Multi-level qualifiers compose: `crate::configurator::stage_apply::run`
     * works. Rust path prefixes (`crate`, `super`, `self`) are stripped so
     * the canonical `crate::module::symbol` form resolves.
     *
     * Resolution order, last part must always equal `node.name`:
     *   1. Suffix-match against `qualifiedName` (handles class-scoped methods
     *      where the extractor builds the qualified name from the AST stack)
     *   2. File-path containment (handles file-derived modules in Rust/
     *      Python — `stage_apply::run` matches a `run` in `stage_apply.rs`)
     */
    private matchesSymbol;
    /**
     * Find ALL definitions matching a name, ranked, so codegraph_node can return
     * every overload instead of guessing one (the wrong guess → a Read). Keepers
     * rank before generated stubs (.pb.go etc.); stable within a group preserves
     * FTS order. Returns [] when nothing matches; a qualified lookup that finds no
     * exact match returns [] rather than a misleading fuzzy file hit (#173); a
     * bare name with no exact match falls back to the single top fuzzy result.
     */
    private findSymbolMatches;
    /**
     * Find ALL symbols matching a name. Used by callers/callees/impact to aggregate
     * results across all matching symbols (e.g., multiple classes with an `execute` method).
     */
    private findAllSymbols;
    /**
     * Truncate output if it exceeds the maximum length
     */
    private truncateOutput;
    private formatSearchResults;
    private formatNodeList;
    /**
     * Relationship label for a non-`calls` edge in callers/callees lists. A
     * function-as-value edge (#756) is the high-signal one: `callers(cb)`
     * showing "via callback registration" tells the agent this is where the
     * callback is WIRED, not where it's invoked.
     */
    private edgeLabel;
    private formatImpact;
    /**
     * Build a compact structural outline of a container symbol from its
     * indexed children (methods, fields, properties, …) — name, kind,
     * line number, and signature — so the agent gets the shape of a class
     * without the full source of every method. Returns '' when the container
     * has no indexed children, so the caller can fall back to full source.
     */
    private buildContainerOutline;
    private formatNodeDetails;
    private textResult;
    private errorResult;
}
export {};
//# sourceMappingURL=tools.d.ts.map