from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "v1-build-and-push-latest.yml"

PRODUCTION_DOCKERFILES = {
    "api": REPO_ROOT / "docker" / "api" / "Dockerfile",
    "video": REPO_ROOT / "docker" / "video-worker" / "Dockerfile",
    "messaging": REPO_ROOT / "docker" / "messaging-worker" / "Dockerfile",
    "ai": REPO_ROOT / "docker" / "ai-worker-cpu" / "Dockerfile",
    "tools": REPO_ROOT / "docker" / "tools-worker" / "Dockerfile",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_base_dependency_copy_does_not_duplicate_the_python_layer() -> None:
    dockerfile = _read(REPO_ROOT / "docker" / "Dockerfile.base")

    assert (
        "COPY --from=builder --chown=appuser:appuser "
        "/root/.local /home/appuser/.local"
    ) in dockerfile
    assert "RUN chown -R appuser:appuser /home/appuser" not in dockerfile


def test_runtime_dependencies_precede_frequently_changed_source() -> None:
    for service, path in PRODUCTION_DOCKERFILES.items():
        dockerfile = _read(path)
        source_copy = "COPY --chown=appuser:appuser academy ./academy"

        assert source_copy in dockerfile, service
        assert "RUN pip install" in dockerfile, service
        assert dockerfile.index("RUN pip install") < dockerfile.index(source_copy), service


def test_runtime_images_build_in_parallel_before_candidate_assembly() -> None:
    workflow = _read(WORKFLOW)
    prepare = workflow.split("\n  prepare-build:\n", maxsplit=1)[1].split(
        "\n  build-runtime-images:\n", maxsplit=1
    )[0]
    runtime_build = workflow.split("\n  build-runtime-images:\n", maxsplit=1)[1].split(
        "\n  build-and-push:\n", maxsplit=1
    )[0]
    assembly = workflow.split("\n  build-and-push:\n", maxsplit=1)[1].split(
        "\n  verify-api-development:\n", maxsplit=1
    )[0]

    assert "base_image_uri: ${{ steps.base-image.outputs.uri }}" in prepare
    assert "strategy:" in runtime_build
    assert "fail-fast: false" in runtime_build
    assert "max-parallel: 5" in runtime_build
    assert runtime_build.count("repository: academy-") == 5
    assert runtime_build.count("uses: docker/build-push-action@") == 1
    assert "build-args: BASE_IMAGE=${{ needs.prepare-build.outputs.base_image_uri }}" in runtime_build
    assert "needs: [detect-changes, prepare-build, build-runtime-images]" in assembly
    assert "Gate newly built images on completed ECR critical scan" in assembly


def test_production_source_copies_have_final_ownership() -> None:
    for service, path in PRODUCTION_DOCKERFILES.items():
        for line in _read(path).splitlines():
            stripped = line.strip()
            if stripped.startswith(("COPY academy", "COPY apps", "COPY libs")):
                raise AssertionError(f"{service} has an ownership-copy regression: {line}")
            if stripped.startswith(("COPY manage.py", "COPY scripts")):
                raise AssertionError(f"{service} has an ownership-copy regression: {line}")


def test_api_refresh_preserves_capacity_and_uses_native_replacement_headroom() -> None:
    workflow = _read(WORKFLOW)
    deploy_api = workflow.split("\n  deploy-api:\n", maxsplit=1)[1].split(
        "\n  deploy-messaging:\n", maxsplit=1
    )[0]

    assert "Ensure API ASG deploy headroom" not in deploy_api
    assert "--min-size" not in deploy_api
    assert "--desired-capacity" not in deploy_api
    assert "Ensure API ASG launch-before-terminate ceiling" in deploy_api
    assert '\\"MinHealthyPercentage\\":100' in deploy_api
    assert '\\"MaxHealthyPercentage\\":200' in deploy_api
    assert '\\"InstanceWarmup\\":${API_INSTANCE_WARMUP}' in deploy_api
    assert "Restore API ASG max-size ceiling" in deploy_api
    assert deploy_api.index("Restore API ASG max-size ceiling") < deploy_api.index(
        "Compensate failed API pin or refresh"
    )


def test_api_refresh_max_healthy_percentage_is_in_the_runtime_ssot() -> None:
    params = _read(REPO_ROOT / "docs" / "ssot" / "params.yaml")
    loader = _read(REPO_ROOT / "scripts" / "v1" / "core" / "ssot.ps1")
    manual_refresh = _read(
        REPO_ROOT / "scripts" / "v1" / "deploy-api-and-verify-workers.ps1"
    )

    assert "instanceRefreshMaxHealthyPercentage: 200" in params
    assert "ApiInstanceRefreshMaxHealthyPercentage" in loader
    assert "ApiInstanceRefreshMaxHealthyPercentage" in manual_refresh
    assert "$refreshRequiredMax = $refreshDesired + 1" in manual_refresh
    assert '"--max-size", [string]$refreshRequiredMax' in manual_refresh
    assert '"--max-size", [string]$refreshOriginalMax' in manual_refresh
    assert "API refresh ceiling readback mismatch after restore" in manual_refresh


def test_api_compensation_refresh_uses_and_restores_native_headroom() -> None:
    compensation = _read(REPO_ROOT / "scripts" / "v1" / "pin-asg-image.ps1")

    assert "$script:ApiInstanceRefreshMinHealthyPercentage" in compensation
    assert "$script:ApiInstanceRefreshMaxHealthyPercentage" in compensation
    assert "$script:ApiInstanceRefreshInstanceWarmup" in compensation
    assert "$requiredMaxSize = $desiredCapacity + 1" in compensation
    assert "restore $Service compensation refresh ceiling" in compensation
    assert "$Service compensation refresh ceiling readback mismatch" in compensation
