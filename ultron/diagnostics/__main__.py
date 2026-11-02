from ultron.diagnostics.scanner import (
    DependencyScanner,
    ModuleScanner,
)

print("\nMODULE HEALTH")
print("================")

modules = ModuleScanner().scan()

failed = 0

for name, result in modules.items():
    if result["status"] == "failed":
        failed += 1
        print("[FAIL]", name, result["error"])


print("\nModules:", len(modules))

print("Failures:", failed)


print("\nDEPENDENCY SCAN")
print("================")

deps = DependencyScanner().scan()

print("Python files scanned:", len(deps))


print("\nTEST STATUS")
print("================")

print({"tests": "disabled in module diagnostics"})
