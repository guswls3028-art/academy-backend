# One-time/idempotent convergence for the isolated API canary database role.
# It never changes the production database schema and never emits credentials.
[CmdletBinding()]
param(
    [ValidatePattern('^/academy/api/env$')]
    [string]$ProductionEnvParameter = "/academy/api/env",
    [ValidatePattern('^/academy/api/(?:preprod|development)/db-credentials$')]
    [string]$CredentialParameter = "/academy/api/preprod/db-credentials",
    [ValidatePattern('^[a-z][a-z0-9_]{2,62}$')]
    [string]$PreprodDatabaseName = "academy_api_preprod",
    [ValidatePattern('^[a-z][a-z0-9_]{2,62}$')]
    [string]$PreprodDatabaseUser = "academy_api_preprod_app",
    [ValidateRange(60, 600)]
    [int]$TimeoutSec = 300,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null

$productionResult = Invoke-AwsJson @(
    "ssm", "get-parameter",
    "--name", $ProductionEnvParameter,
    "--with-decryption",
    "--region", $script:Region,
    "--output", "json"
)
if (-not $productionResult -or -not $productionResult.Parameter.Value) {
    throw "Production API env is missing or unreadable."
}
try {
    $production = [string]$productionResult.Parameter.Value | ConvertFrom-Json
} catch {
    throw "Production API env is not valid JSON."
}
$productionDatabaseName = [string]$production.DB_NAME
$productionDatabaseUser = [string]$production.DB_USER
if (-not $productionDatabaseName -or $productionDatabaseName -eq $PreprodDatabaseName) {
    throw "Production and preprod database names must be distinct."
}
if (-not $productionDatabaseUser -or $productionDatabaseUser -eq $PreprodDatabaseUser) {
    throw "Production and preprod database users must be distinct."
}

$credentialResult = Invoke-AwsJson @(
    "ssm", "get-parameter",
    "--name", $CredentialParameter,
    "--with-decryption",
    "--region", $script:Region,
    "--output", "json"
)
if (-not $credentialResult) {
    $randomBytes = [byte[]]::new(48)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
    $password = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    $credentialValue = [ordered]@{
        DB_USER = $PreprodDatabaseUser
        DB_PASSWORD = $password
    } | ConvertTo-Json -Compress
    $created = Invoke-AwsJson @(
        "ssm", "put-parameter",
        "--name", $CredentialParameter,
        "--description", "Dedicated credentials for the isolated Academy API preprod database",
        "--type", "SecureString",
        "--tier", "Standard",
        "--value", $credentialValue,
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $created -or -not $created.Version) {
        throw "Failed to create the dedicated API preprod credential parameter."
    }
    $credentialResult = Invoke-AwsJson @(
        "ssm", "get-parameter",
        "--name", $CredentialParameter,
        "--with-decryption",
        "--region", $script:Region,
        "--output", "json"
    )
}
if (
    -not $credentialResult -or
    [string]$credentialResult.Parameter.Type -ne "SecureString" -or
    -not $credentialResult.Parameter.Value
) {
    throw "API preprod credential parameter must be a readable SecureString."
}
try {
    $credential = [string]$credentialResult.Parameter.Value | ConvertFrom-Json
} catch {
    throw "API preprod credential parameter is not valid JSON."
}
if (
    [string]$credential.DB_USER -ne $PreprodDatabaseUser -or
    -not $credential.DB_PASSWORD -or
    ([string]$credential.DB_PASSWORD).Length -lt 32
) {
    throw "API preprod credential parameter is not bound to the expected dedicated role."
}

$asg = Invoke-AwsJson @(
    "autoscaling", "describe-auto-scaling-groups",
    "--auto-scaling-group-names", $script:ApiASGName,
    "--region", $script:Region,
    "--output", "json"
)
$instance = @(
    $asg.AutoScalingGroups[0].Instances |
        Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" } |
        Select-Object -First 1
)[0]
$instanceId = [string]$instance.InstanceId
if (-not $instanceId) {
    throw "No healthy production API instance is available for the scoped preprod DB convergence."
}

$python = @'
import json

from django.db import connection
from psycopg2 import sql

ROLE = "__PREPROD_ROLE__"
PREPROD_DB = "__PREPROD_DATABASE__"
PRODUCTION_DB = "__PRODUCTION_DATABASE__"
CREDENTIAL_PATH = "/tmp/academy_api_preprod_db_credentials.json"

with open(CREDENTIAL_PATH, encoding="utf-8") as handle:
    credential = json.load(handle)
password = str(credential.get("DB_PASSWORD", ""))
if credential.get("DB_USER") != ROLE or len(password) < 32:
    raise SystemExit("invalid dedicated preprod credential contract")

connection.ensure_connection()
connection.set_autocommit(True)
with connection.cursor() as cursor:
    cursor.execute("SELECT current_database(), current_user")
    current_database, current_user = cursor.fetchone()
    if current_database != PRODUCTION_DB or current_user == ROLE:
        raise SystemExit("unexpected production database bootstrap identity")

    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
    role_exists = cursor.fetchone() is not None
    role_sql = sql.SQL(
        "ALTER ROLE {} WITH LOGIN NOCREATEDB NOCREATEROLE "
        "NOINHERIT PASSWORD %s"
        if role_exists
        else "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOINHERIT PASSWORD %s"
    ).format(sql.Identifier(ROLE))
    cursor.execute(role_sql, [password])

    cursor.execute(
        """
        SELECT parent.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        WHERE member.rolname = %s
        """,
        [ROLE],
    )
    for (parent_role,) in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(parent_role),
                sql.Identifier(ROLE),
            )
        )

    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [PREPROD_DB])
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(PREPROD_DB),
                sql.Identifier(ROLE),
            )
        )

params = connection.get_connection_params().copy()
params["dbname"] = PREPROD_DB
preprod = connection.Database.connect(**params)
reowned = 0
schema_owner = ""
schema_usage = False
schema_create = False
vector_extension_version = ""
try:
    preprod.autocommit = True
    with preprod.cursor() as cursor:
        # pgvector is a trusted runtime dependency but RDS permits only the
        # privileged bootstrap identity to install it. Application migrations
        # can then execute CREATE EXTENSION IF NOT EXISTS without elevated
        # privileges while every application table remains role-owned.
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute(
            sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                sql.Identifier(ROLE)
            )
        )
        cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cursor.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(ROLE)
            )
        )
        cursor.execute(
            """
            SELECT namespace.nspname, relation.relname, relation.relkind
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname NOT LIKE 'pg_toast%%'
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
              AND pg_get_userbyid(relation.relowner) <> %s
            ORDER BY namespace.nspname, relation.relname
            """,
            [ROLE],
        )
        relations = cursor.fetchall()
        commands = {
            "r": "ALTER TABLE {}.{} OWNER TO {}",
            "p": "ALTER TABLE {}.{} OWNER TO {}",
            "S": "ALTER SEQUENCE {}.{} OWNER TO {}",
            "v": "ALTER VIEW {}.{} OWNER TO {}",
            "m": "ALTER MATERIALIZED VIEW {}.{} OWNER TO {}",
            "f": "ALTER FOREIGN TABLE {}.{} OWNER TO {}",
        }
        for schema_name, relation_name, relation_kind in relations:
            cursor.execute(
                sql.SQL(commands[relation_kind]).format(
                    sql.Identifier(schema_name),
                    sql.Identifier(relation_name),
                    sql.Identifier(ROLE),
                )
            )
            reowned += 1
        cursor.execute(
            """
            SELECT
                pg_get_userbyid(namespace.nspowner),
                has_schema_privilege(%s, 'public', 'USAGE'),
                has_schema_privilege(%s, 'public', 'CREATE')
            FROM pg_namespace namespace
            WHERE namespace.nspname = 'public'
            """,
            [ROLE, ROLE],
        )
        schema_owner, schema_usage, schema_create = cursor.fetchone()
        cursor.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
        extension_row = cursor.fetchone()
        vector_extension_version = extension_row[0] if extension_row else ""
finally:
    preprod.close()

with connection.cursor() as cursor:
    cursor.execute(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(PREPROD_DB),
            sql.Identifier(ROLE),
        )
    )
    cursor.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
            sql.Identifier(PREPROD_DB)
        )
    )
    cursor.execute(
        sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
            sql.Identifier(PREPROD_DB),
            sql.Identifier(ROLE),
        )
    )
    cursor.execute(
        sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
            sql.Identifier(PRODUCTION_DB),
            sql.Identifier(ROLE),
        )
    )
    cursor.execute(
        """
        SELECT
            has_database_privilege(%s, %s, 'CONNECT'),
            has_database_privilege(%s, %s, 'CONNECT'),
            role.rolsuper,
            role.rolcreatedb,
            role.rolcreaterole,
            role.rolinherit
        FROM pg_roles role
        WHERE role.rolname = %s
        """,
        [ROLE, PRODUCTION_DB, ROLE, PREPROD_DB, ROLE],
    )
    (
        production_connect,
        preprod_connect,
        is_superuser,
        can_create_db,
        can_create_role,
        can_inherit,
    ) = cursor.fetchone()

if (
    production_connect
    or not preprod_connect
    or is_superuser
    or can_create_db
    or can_create_role
    or can_inherit
    or schema_owner != ROLE
    or not schema_usage
    or not schema_create
    or not vector_extension_version
):
    raise SystemExit("dedicated preprod database privilege verification failed")

print(
    json.dumps(
        {
            "status": "PREPROD_DATABASE_CONVERGED",
            "database": PREPROD_DB,
            "role": ROLE,
            "production_connect": production_connect,
            "preprod_connect": preprod_connect,
            "public_schema_owner": schema_owner,
            "public_schema_usage": schema_usage,
            "public_schema_create": schema_create,
            "vector_extension_version": vector_extension_version,
            "objects_reowned": reowned,
        }
    )
)
'@
$python = $python.Replace("__PREPROD_ROLE__", $PreprodDatabaseUser)
$python = $python.Replace("__PREPROD_DATABASE__", $PreprodDatabaseName)
$python = $python.Replace("__PRODUCTION_DATABASE__", $productionDatabaseName)
$python = $python.Replace("`r", "")
$pythonB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
$remote = @'
set -euo pipefail
host_code=/tmp/academy_api_preprod_db_converge.py
host_credential=/tmp/academy_api_preprod_db_credentials.json
container_credential=/tmp/academy_api_preprod_db_credentials.json
cleanup() {
  rm -f "$host_code" "$host_credential"
  docker exec -u root academy-api rm -f "$container_credential" >/dev/null 2>&1 || true
}
trap cleanup EXIT
umask 077
echo '__PYTHON_B64__' | base64 -d > "$host_code"
aws ssm get-parameter \
  --name '__CREDENTIAL_PARAMETER__' \
  --with-decryption \
  --query Parameter.Value \
  --output text \
  --region '__REGION__' > "$host_credential"
test -s "$host_credential"
docker cp "$host_credential" "academy-api:$container_credential" >/dev/null
docker exec -u root academy-api chown appuser:appuser "$container_credential"
docker exec -u root academy-api chmod 600 "$container_credential"
docker exec -i academy-api python manage.py shell < "$host_code"
'@
$remote = $remote.Replace("__PYTHON_B64__", $pythonB64)
$remote = $remote.Replace("__CREDENTIAL_PARAMETER__", $CredentialParameter)
$remote = $remote.Replace("__REGION__", $script:Region)
$remote = $remote.Replace("`r", "")
$remoteB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
$remoteCommand = "echo '$remoteB64' | base64 -d | bash"
$paramsRef = Convert-JsonArgToFileRef (
    @{
        commands = @($remoteCommand)
        executionTimeout = @([string]$TimeoutSec)
    } | ConvertTo-Json -Compress
)
$paramsFile = $paramsRef -replace '^file://', ''
try {
    $sent = Invoke-AwsJson @(
        "ssm", "send-command",
        "--instance-ids", $instanceId,
        "--document-name", "AWS-RunShellScript",
        "--parameters", $paramsRef,
        "--timeout-seconds", [string]$TimeoutSec,
        "--region", $script:Region,
        "--comment", "Converge isolated Academy API preprod database role",
        "--output", "json"
    )
} finally {
    Remove-TempFiles @($paramsFile)
}
$commandId = [string]$sent.Command.CommandId
if (-not $commandId) { throw "Preprod database convergence returned no SSM command id." }

$invocation = $null
for ($elapsed = 0; $elapsed -lt $TimeoutSec; $elapsed += 3) {
    Start-Sleep -Seconds 3
    $invocation = Invoke-AwsJson @(
        "ssm", "get-command-invocation",
        "--command-id", $commandId,
        "--instance-id", $instanceId,
        "--region", $script:Region,
        "--output", "json"
    )
    if ([string]$invocation.Status -eq "Success") { break }
    if ([string]$invocation.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) { break }
}
if ([string]$invocation.Status -ne "Success") {
    $stderr = ([string]$invocation.StandardErrorContent).Trim()
    throw "Preprod database convergence failed: status=$($invocation.Status) stderr=$stderr"
}
$proof = ([string]$invocation.StandardOutputContent).Trim()
if ($proof -notmatch '"status": "PREPROD_DATABASE_CONVERGED"' -or $proof -notmatch '"production_connect": false') {
    throw "Preprod database convergence returned no fail-closed privilege proof."
}
Write-Host $proof -ForegroundColor Green
