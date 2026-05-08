"""Compare the most recent revision against its parent to explain a similarity-score change."""
import sys
sys.path.insert(0, "/app")
import json
from database.crud import Database

USER_EMAIL = "sdogan1334@gmail.com"


def main():
    db = Database()
    cur = db.cursor()
    try:
        cur.execute("SELECT id, organization_id FROM users WHERE email = %s", (USER_EMAIL,))
        u = cur.fetchone()
        if not u:
            print(f"no user {USER_EMAIL}")
            return
        org_id = str(u["organization_id"])
        print(f"user={USER_EMAIL} org={org_id[:8]}\n")

        # Find the most recent REVISION for this org
        cur.execute(
            """
            SELECT id, parent_image_id, project_id, style, similarity_score, is_safe,
                   audit_status, visual_breakdown, image_path, generation_kind, created_at
            FROM generated_images
            WHERE org_id = %s
              AND generation_kind = 'REVISION'
              AND audit_status = 'completed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (org_id,),
        )
        rev = cur.fetchone()
        if not rev:
            print("no completed revision found")
            return

        rev_id = str(rev["id"])
        parent_id = str(rev["parent_image_id"]) if rev.get("parent_image_id") else None
        print(f"REVISION:")
        print(f"  id={rev_id[:8]}")
        print(f"  project={str(rev['project_id'])[:8]}")
        print(f"  style={rev['style']}")
        print(f"  similarity_score={rev['similarity_score']}")
        print(f"  is_safe={rev['is_safe']}")
        print(f"  audit_status={rev['audit_status']}")
        print(f"  created_at={rev['created_at']}")
        print(f"  parent_image_id={parent_id[:8] if parent_id else None}")

        breakdown_rev = rev.get("visual_breakdown")
        if isinstance(breakdown_rev, str):
            try:
                breakdown_rev = json.loads(breakdown_rev)
            except Exception:
                breakdown_rev = None
        print(f"  visual_breakdown: {json.dumps(breakdown_rev, indent=2, ensure_ascii=False)[:1500]}\n")

        if not parent_id:
            print("(no parent on this row — cannot compare)")
            return

        cur.execute(
            """
            SELECT id, parent_image_id, project_id, style, similarity_score, is_safe,
                   audit_status, visual_breakdown, image_path, generation_kind, created_at
            FROM generated_images
            WHERE id = %s::uuid AND org_id = %s
            """,
            (parent_id, org_id),
        )
        par = cur.fetchone()
        if not par:
            print("parent row not found in DB")
            return
        print(f"PARENT (the logo the user revised):")
        print(f"  id={parent_id[:8]}")
        print(f"  style={par['style']}")
        print(f"  similarity_score={par['similarity_score']}")
        print(f"  is_safe={par['is_safe']}")
        print(f"  audit_status={par['audit_status']}")
        print(f"  generation_kind={par['generation_kind']}")
        print(f"  created_at={par['created_at']}")

        breakdown_par = par.get("visual_breakdown")
        if isinstance(breakdown_par, str):
            try:
                breakdown_par = json.loads(breakdown_par)
            except Exception:
                breakdown_par = None
        print(f"  visual_breakdown: {json.dumps(breakdown_par, indent=2, ensure_ascii=False)[:1500]}\n")

        # Headline comparison
        print("=== headline comparison ===")
        print(f"  parent score:   {par['similarity_score']}")
        print(f"  revision score: {rev['similarity_score']}")
        diff = (rev['similarity_score'] or 0) - (par['similarity_score'] or 0)
        print(f"  delta:          {diff:+.2f}")
        print(f"  parent closest_match_name:   {(breakdown_par or {}).get('closest_match_name')}")
        print(f"  revision closest_match_name: {(breakdown_rev or {}).get('closest_match_name')}")
        print(f"  parent llm_risk_score:       {(breakdown_par or {}).get('llm_risk_score')}")
        print(f"  revision llm_risk_score:     {(breakdown_rev or {}).get('llm_risk_score')}")
        print(f"  parent risk_source:          {(breakdown_par or {}).get('risk_source')}")
        print(f"  revision risk_source:        {(breakdown_rev or {}).get('risk_source')}")
    finally:
        db.conn.close()


main()
