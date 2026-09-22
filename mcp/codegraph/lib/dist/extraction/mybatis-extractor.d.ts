import { ExtractionResult } from '../types';
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
export declare class MyBatisExtractor {
    private filePath;
    private source;
    private nodes;
    private edges;
    private unresolvedReferences;
    private errors;
    private lineStarts;
    constructor(filePath: string, source: string);
    extract(): ExtractionResult;
    private createFileNode;
    /**
     * Find the `<mapper namespace="X">` opening tag. Returns the namespace and
     * the byte offsets of the body (between the opening and closing tag) so
     * statement extraction can be scoped to mapper contents.
     */
    private findMapperRoot;
    private extractMapper;
    private buildSignature;
    private previewSql;
    private computeLineStarts;
    private getLineNumber;
}
//# sourceMappingURL=mybatis-extractor.d.ts.map