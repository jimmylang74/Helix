"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MyBatisExtractor = void 0;
const tree_sitter_helpers_1 = require("./tree-sitter-helpers");
/**
 * MyBatisExtractor — parses MyBatis mapper XML files.
 *
 * MyBatis splits a DAO interface across two files: a Java interface (parsed by
 * tree-sitter) declares the method, and an XML mapper file holds the SQL keyed
 * by `<namespace>` (the fully-qualified Java type name) and `id` (the method
 * name). Without the XML side in the graph, `trace(Controller, ...DAO.method)`
 * dead-ends at the interface method — the SQL it actually runs is invisible,
 * and "what does this query touch" / "where is this column written" can't be
 * answered.
 *
 * This extractor emits one method-shaped node per `<select|insert|update|
 * delete>` and per `<sql>` fragment, qualified as `<namespace>::<id>` so the
 * MyBatis framework synthesizer (`src/resolution/frameworks/mybatis.ts`) can
 * link the matching Java method → XML statement by suffix-matching qualified
 * names. `<include refid="...">` inside a statement yields an unresolved
 * reference to the SQL fragment, also keyed by `<namespace>::<refid>`.
 *
 * Non-mapper XML (Maven `pom.xml`, Spring beans XML, `web.xml`, log4j config,
 * etc.) is detected by the absence of a `<mapper namespace="...">` root and
 * returns just a file node — we still need the file row so the watcher can
 * track it, but we emit no symbols.
 */
class MyBatisExtractor {
    filePath;
    source;
    nodes = [];
    edges = [];
    unresolvedReferences = [];
    errors = [];
    lineStarts = [];
    constructor(filePath, source) {
        this.filePath = filePath;
        this.source = source;
        this.computeLineStarts();
    }
    extract() {
        const startTime = Date.now();
        const fileNode = this.createFileNode();
        try {
            const mapperMatch = this.findMapperRoot();
            if (mapperMatch) {
                this.extractMapper(fileNode.id, mapperMatch.namespace, mapperMatch.bodyStart, mapperMatch.bodyEnd);
            }
        }
        catch (error) {
            this.errors.push({
                message: `MyBatis extraction error: ${error instanceof Error ? error.message : String(error)}`,
                severity: 'error',
                code: 'parse_error',
            });
        }
        return {
            nodes: this.nodes,
            edges: this.edges,
            unresolvedReferences: this.unresolvedReferences,
            errors: this.errors,
            durationMs: Date.now() - startTime,
        };
    }
    createFileNode() {
        const lines = this.source.split('\n');
        const id = (0, tree_sitter_helpers_1.generateNodeId)(this.filePath, 'file', this.filePath, 1);
        const node = {
            id,
            kind: 'file',
            name: this.filePath.split('/').pop() || this.filePath,
            qualifiedName: this.filePath,
            filePath: this.filePath,
            language: 'xml',
            startLine: 1,
            endLine: lines.length || 1,
            startColumn: 0,
            endColumn: lines[lines.length - 1]?.length ?? 0,
            updatedAt: Date.now(),
        };
        this.nodes.push(node);
        return node;
    }
    /**
     * Find the `<mapper namespace="X">` opening tag. Returns the namespace and
     * the byte offsets of the body (between the opening and closing tag) so
     * statement extraction can be scoped to mapper contents.
     */
    findMapperRoot() {
        const open = /<mapper\b([^>]*)>/.exec(this.source);
        if (!open)
            return null;
        const attrs = open[1] ?? '';
        const nsMatch = /\bnamespace\s*=\s*"([^"]+)"/.exec(attrs);
        if (!nsMatch)
            return null;
        const bodyStart = open.index + open[0].length;
        const closeIdx = this.source.indexOf('</mapper>', bodyStart);
        const bodyEnd = closeIdx >= 0 ? closeIdx : this.source.length;
        return { namespace: nsMatch[1], bodyStart, bodyEnd };
    }
    extractMapper(fileNodeId, namespace, bodyStart, bodyEnd) {
        const body = this.source.slice(bodyStart, bodyEnd);
        // Match each top-level statement-shaped element. The body may have nested
        // tags (`<if>`, `<foreach>`, `<include>`), so we scan with a regex that
        // pairs an opening tag to its matching close — the simple form below works
        // because MyBatis statement elements are not themselves nested.
        const stmtRegex = /<(select|insert|update|delete|sql)\b([^>]*)>([\s\S]*?)<\/\1>/g;
        let m;
        while ((m = stmtRegex.exec(body)) !== null) {
            const elemType = m[1];
            const attrs = m[2] ?? '';
            const elemBody = m[3] ?? '';
            const idMatch = /\bid\s*=\s*"([^"]+)"/.exec(attrs);
            if (!idMatch)
                continue;
            const id = idMatch[1];
            const absoluteIndex = bodyStart + m.index;
            const startLine = this.getLineNumber(absoluteIndex);
            const endLine = this.getLineNumber(absoluteIndex + m[0].length);
            const qualified = `${namespace}::${id}`;
            const isSqlFragment = elemType === 'sql';
            const nodeId = (0, tree_sitter_helpers_1.generateNodeId)(this.filePath, 'method', qualified, startLine);
            const node = {
                id: nodeId,
                kind: 'method',
                name: id,
                qualifiedName: qualified,
                filePath: this.filePath,
                language: 'xml',
                signature: this.buildSignature(elemType, attrs, isSqlFragment),
                startLine,
                endLine,
                startColumn: 0,
                endColumn: 0,
                docstring: this.previewSql(elemBody),
                updatedAt: Date.now(),
            };
            this.nodes.push(node);
            this.edges.push({ source: fileNodeId, target: nodeId, kind: 'contains' });
            // <include refid="X"/> → reference to the SQL fragment in this mapper
            // (or in another mapper, when the refid is qualified — `ns.X`).
            const includeRegex = /<include\b[^>]*\brefid\s*=\s*"([^"]+)"/g;
            let inc;
            while ((inc = includeRegex.exec(elemBody)) !== null) {
                const refid = inc[1];
                const refQualified = refid.includes('.') ? refid.replace(/\./g, '::') : `${namespace}::${refid}`;
                const includeOffset = absoluteIndex + (m[0].length - m[3].length - `</${elemType}>`.length) + inc.index;
                const line = this.getLineNumber(includeOffset);
                this.unresolvedReferences.push({
                    fromNodeId: nodeId,
                    referenceName: refQualified,
                    referenceKind: 'references',
                    line,
                    column: 0,
                });
            }
        }
    }
    buildSignature(elemType, attrs, isSqlFragment) {
        if (isSqlFragment)
            return '<sql>';
        const verb = elemType.toUpperCase();
        const result = /\bresultType\s*=\s*"([^"]+)"/.exec(attrs)?.[1];
        const param = /\bparameterType\s*=\s*"([^"]+)"/.exec(attrs)?.[1];
        const parts = [verb];
        if (param)
            parts.push(`param=${param}`);
        if (result)
            parts.push(`result=${result}`);
        return parts.join(' ');
    }
    previewSql(body) {
        return body.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
    }
    computeLineStarts() {
        this.lineStarts = [0];
        for (let i = 0; i < this.source.length; i++) {
            if (this.source.charCodeAt(i) === 10)
                this.lineStarts.push(i + 1);
        }
    }
    getLineNumber(offset) {
        // Binary search
        let lo = 0;
        let hi = this.lineStarts.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >>> 1;
            if (this.lineStarts[mid] <= offset)
                lo = mid;
            else
                hi = mid - 1;
        }
        return lo + 1;
    }
}
exports.MyBatisExtractor = MyBatisExtractor;
//# sourceMappingURL=mybatis-extractor.js.map