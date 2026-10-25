import uuid
import secrets
from datetime import datetime, timedelta, timezone
from database import get_db_connection
from services.email_service import EmailService

class TeamService:
    @staticmethod
    def create_team(owner_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Team name is required.")

        team_id = str(uuid.uuid4())
        conn = get_db_connection()
        conn.execute("INSERT INTO teams (id, name, owner_id) VALUES (?, ?, ?)", (team_id, name, owner_id))
        
        # Add owner to team_members
        member_id = str(uuid.uuid4())
        conn.execute("INSERT INTO team_members (id, team_id, user_id, role) VALUES (?, ?, ?, 'owner')", (member_id, team_id, owner_id))
        conn.commit()
        conn.close()

        return {"team_id": team_id, "name": name, "role": "owner"}

    @staticmethod
    def get_user_teams(user_id):
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT t.id, t.name, t.owner_id, t.plan, tm.role, tm.joined_at,
                      (SELECT COUNT(*) FROM team_members WHERE team_id = t.id) as member_count
               FROM teams t
               JOIN team_members tm ON t.id = tm.team_id
               WHERE tm.user_id = ?""",
            (user_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_team_members(team_id, user_id):
        conn = get_db_connection()
        member_check = conn.execute("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, user_id)).fetchone()
        if not member_check:
            conn.close()
            raise PermissionError("Access denied: You are not a member of this team.")

        members = conn.execute(
            """SELECT tm.id as membership_id, tm.role, tm.joined_at, u.id as user_id, u.email, u.display_name, u.avatar_url
               FROM team_members tm
               JOIN users u ON tm.user_id = u.id
               WHERE tm.team_id = ?
               ORDER BY CASE tm.role WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 ELSE 3 END""",
            (team_id,)
        ).fetchall()

        invites = conn.execute(
            "SELECT id, email, role, token, expires_at FROM team_invitations WHERE team_id = ? AND accepted = 0",
            (team_id,)
        ).fetchall()

        conn.close()
        return {
            "members": [dict(m) for m in members],
            "pending_invitations": [dict(i) for i in invites],
            "caller_role": member_check['role']
        }

    @staticmethod
    def invite_member(team_id, actor_id, email, role="member"):
        email = email.strip().lower()
        if not email or '@' not in email:
            raise ValueError("Valid recipient email is required.")
        if role not in ["admin", "member"]:
            role = "member"

        conn = get_db_connection()
        actor = conn.execute("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, actor_id)).fetchone()
        if not actor or actor['role'] not in ['owner', 'admin']:
            conn.close()
            raise PermissionError("Only team owners and admins can send invitations.")

        team_row = conn.execute("SELECT owner_id, name FROM teams WHERE id = ?", (team_id,)).fetchone()
        if not team_row:
            conn.close()
            raise ValueError("Team not found.")

        # Check owner subscription limits (Free trial: 2 seats max; Monthly: 5 seats; Annual: 15 seats)
        from services.billing_service import BillingService
        owner_sub = BillingService.get_user_subscription(team_row["owner_id"])
        plan_id = owner_sub.get("plan_id", "free")

        if plan_id == "premium_annual":
            max_members = 15
        elif plan_id == "premium_monthly":
            max_members = 5
        else:
            max_members = 2  # Free trial seat limit: 1 owner + 1 invited member

        current_members = conn.execute("SELECT COUNT(*) as c FROM team_members WHERE team_id = ?", (team_id,)).fetchone()["c"]
        pending_invites = conn.execute("SELECT COUNT(*) as c FROM team_invitations WHERE team_id = ? AND accepted = 0", (team_id,)).fetchone()["c"]

        if (current_members + pending_invites) >= max_members:
            conn.close()
            raise ValueError(f"Team member seat limit reached for {owner_sub['plan_name']}. Maximum {max_members} seats permitted.")

        # Check if email is already a member
        existing_member = conn.execute("""
            SELECT tm.id FROM team_members tm
            JOIN users u ON tm.user_id = u.id
            WHERE tm.team_id = ? AND u.email = ?
        """, (team_id, email)).fetchone()
        if existing_member:
            conn.close()
            raise ValueError("This user is already a member of this team.")

        # Check if already invited
        existing_invite = conn.execute("SELECT id FROM team_invitations WHERE team_id = ? AND email = ? AND accepted = 0", (team_id, email)).fetchone()
        if existing_invite:
            conn.close()
            raise ValueError("An active invitation has already been sent to this email address.")

        token = secrets.token_hex(16)
        inv_id = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        conn.execute(
            "INSERT INTO team_invitations (id, team_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (inv_id, team_id, email, role, token, expires_at)
        )
        conn.commit()

        # Fetch actor's display name for email
        actor_user = conn.execute("SELECT display_name, email FROM users WHERE id = ?", (actor_id,)).fetchone()
        inviter_name = actor_user["display_name"] if actor_user else "Team Admin"
        conn.close()

        invite_link = f"/teams/join?token={token}"
        email_sent = False
        try:
            email_sent = EmailService.send_team_invitation(
                to_email=email,
                inviter_name=inviter_name,
                team_name=team_row["name"],
                invite_link=invite_link
            )
        except Exception:
            email_sent = False

        return {
            "invitation_id": inv_id,
            "email": email,
            "role": role,
            "invite_token": token,
            "invite_link": invite_link,
            "email_sent": email_sent,
            "message": f"Invitation generated successfully. {'Email notification sent.' if email_sent else 'Email delivery unconfigured; share the invite link manually.'}"
        }

    @staticmethod
    def accept_invitation(token, user_id):
        conn = get_db_connection()
        inv = conn.execute("SELECT * FROM team_invitations WHERE token = ? AND accepted = 0", (token,)).fetchone()
        if not inv:
            conn.close()
            raise ValueError("Invalid or expired invitation token.")

        try:
            exp = datetime.fromisoformat(inv['expires_at'])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                conn.close()
                raise ValueError("This invitation has expired.")
        except Exception as e:
            if "expired" in str(e):
                raise e
            pass

        # Check if already a member
        existing = conn.execute("SELECT id FROM team_members WHERE team_id = ? AND user_id = ?", (inv['team_id'], user_id)).fetchone()
        if existing:
            conn.execute("UPDATE team_invitations SET accepted = 1 WHERE id = ?", (inv['id'],))
            conn.commit()
            conn.close()
            return {"team_id": inv['team_id'], "message": "Already a member of this team."}

        member_id = str(uuid.uuid4())
        conn.execute("INSERT INTO team_members (id, team_id, user_id, role) VALUES (?, ?, ?, ?)", (member_id, inv['team_id'], user_id, inv['role']))
        conn.execute("UPDATE team_invitations SET accepted = 1 WHERE id = ?", (inv['id'],))
        conn.commit()
        conn.close()

        return {"team_id": inv['team_id'], "role": inv['role'], "message": "Successfully joined team."}

    @staticmethod
    def update_member_role(team_id, actor_id, target_user_id, new_role):
        if new_role not in ["admin", "member"]:
            raise ValueError("Invalid role specified.")

        conn = get_db_connection()
        actor = conn.execute("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, actor_id)).fetchone()
        if not actor or actor['role'] != 'owner':
            conn.close()
            raise PermissionError("Only team owners can modify member roles.")

        conn.execute("UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?", (new_role, team_id, target_user_id))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def remove_member(team_id, actor_id, target_user_id):
        conn = get_db_connection()
        actor = conn.execute("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, actor_id)).fetchone()
        if not actor or actor['role'] not in ['owner', 'admin']:
            conn.close()
            raise PermissionError("Only owners and admins can remove team members.")

        target = conn.execute("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, target_user_id)).fetchone()
        if target and target['role'] == 'owner':
            conn.close()
            raise ValueError("Team owner cannot be removed.")

        conn.execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, target_user_id))
        conn.commit()
        conn.close()
        return True
