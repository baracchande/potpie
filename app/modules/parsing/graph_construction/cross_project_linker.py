from typing import Dict, List, Optional

from neo4j import GraphDatabase
from app.modules.utils.logger import setup_logger

logger = setup_logger(__name__)


class CrossProjectLinker:
    """
    Creates relationships between nodes in different projects in the knowledge graph.

    Supports three matching strategies:
    1. Name + type matching (exact function/class name across repos)
    2. Import resolution (references in repo A resolved to definitions in repo B)
    3. Semantic similarity via embeddings (similar docstrings/implementations)
    """

    def __init__(self, neo4j_uri: str, neo4j_username: str, neo4j_password: str):
        self.driver = GraphDatabase.driver(
            neo4j_uri, auth=(neo4j_username, neo4j_password)
        )

    def close(self):
        if self.driver:
            self.driver.close()

    def link_projects(self, project_ids: List[str]) -> Dict[str, int]:
        """
        Find matching nodes across projects and create CROSS_PROJECT_REFERENCES relationships.

        Args:
            project_ids: List of project UUIDs to link together.

        Returns:
            Dict with counts per strategy: {"name": N, "import_resolution": M, "semantic": K}
        """
        if len(project_ids) < 2:
            logger.warning("link_projects requires at least 2 project IDs")
            return {"name": 0, "import_resolution": 0, "semantic": 0}

        results = {}
        results["name"] = self._link_by_name(project_ids)
        results["import_resolution"] = self._link_by_import_resolution(project_ids)
        results["semantic"] = self._link_by_semantic_similarity(project_ids)

        total = sum(results.values())
        logger.info(
            f"CrossProjectLinker: linked {total} pairs across {len(project_ids)} projects. "
            f"Breakdown: {results}"
        )
        return results

    def _link_by_name(self, project_ids: List[str]) -> int:
        """
        Strategy 1: Match FUNCTION/CLASS/INTERFACE nodes with the same name and type
        across different projects.
        """
        query = """
        MATCH (a:NODE), (b:NODE)
        WHERE a.repoId IN $project_ids
          AND b.repoId IN $project_ids
          AND a.repoId <> b.repoId
          AND a.name = b.name
          AND a.name IS NOT NULL
          AND a.type = b.type
          AND a.type IN ['FUNCTION', 'CLASS', 'INTERFACE']
        MERGE (a)-[r:CROSS_PROJECT_REFERENCES {
            match_type: 'name',
            source_project: a.repoId,
            target_project: b.repoId
        }]->(b)
        RETURN count(r) AS linked
        """
        with self.driver.session() as session:
            result = session.run(query, project_ids=project_ids)
            record = result.single()
            return record["linked"] if record else 0

    def _link_by_import_resolution(self, project_ids: List[str]) -> int:
        """
        Strategy 2: Match REFERENCES edges in repo A whose target name exists as a
        FUNCTION or CLASS node in a different repo B.
        """
        query = """
        MATCH (a:NODE)-[:REFERENCES]->(ref:NODE)
        WHERE a.repoId IN $project_ids
          AND ref.repoId = a.repoId
          AND ref.name IS NOT NULL
        WITH a, ref.name AS ref_name, a.repoId AS source_project
        MATCH (b:NODE)
        WHERE b.repoId IN $project_ids
          AND b.repoId <> source_project
          AND b.name = ref_name
          AND b.type IN ['FUNCTION', 'CLASS']
        MERGE (a)-[r:CROSS_PROJECT_REFERENCES {
            match_type: 'import_resolution',
            source_project: source_project,
            target_project: b.repoId,
            referenced_name: ref_name
        }]->(b)
        RETURN count(r) AS linked
        """
        with self.driver.session() as session:
            result = session.run(query, project_ids=project_ids)
            record = result.single()
            return record["linked"] if record else 0

    def _link_by_semantic_similarity(
        self, project_ids: List[str], similarity_threshold: float = 0.85
    ) -> int:
        """
        Strategy 3: Use the existing 384-dim docstring embeddings to find semantically
        similar nodes across different projects. Requires a Neo4j vector index named
        'docstring_embedding'.
        """
        query = """
        MATCH (a:NODE)
        WHERE a.repoId IN $project_ids
          AND a.embedding IS NOT NULL
          AND a.type IN ['FUNCTION', 'CLASS']
        WITH a
        CALL db.index.vector.queryNodes('docstring_embedding', 10, a.embedding)
        YIELD node AS b, score
        WHERE b.repoId IN $project_ids
          AND b.repoId <> a.repoId
          AND score >= $threshold
          AND b.type IN ['FUNCTION', 'CLASS']
        MERGE (a)-[r:CROSS_PROJECT_REFERENCES {
            match_type: 'semantic',
            source_project: a.repoId,
            target_project: b.repoId,
            similarity_score: score
        }]->(b)
        RETURN count(r) AS linked
        """
        try:
            with self.driver.session() as session:
                result = session.run(
                    query,
                    project_ids=project_ids,
                    threshold=similarity_threshold,
                )
                record = result.single()
                return record["linked"] if record else 0
        except Exception as e:
            # Vector index may not exist — skip semantic linking gracefully
            logger.warning(
                f"Semantic linking skipped (vector index may not exist): {e}"
            )
            return 0

    def remove_cross_project_links(self, project_ids: List[str]) -> int:
        """Remove all CROSS_PROJECT_REFERENCES edges for the given projects."""
        query = """
        MATCH (a:NODE)-[r:CROSS_PROJECT_REFERENCES]->(b:NODE)
        WHERE a.repoId IN $project_ids OR b.repoId IN $project_ids
        DELETE r
        RETURN count(r) AS removed
        """
        with self.driver.session() as session:
            result = session.run(query, project_ids=project_ids)
            record = result.single()
            removed = record["removed"] if record else 0
            logger.info(f"Removed {removed} CROSS_PROJECT_REFERENCES edges")
            return removed
