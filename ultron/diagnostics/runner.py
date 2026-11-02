from ultron.diagnostics.runner import DiagnosticRunner

print("\nULTRON DIAGNOSTICS")
print("==================")


report = DiagnosticRunner().run()


for section, result in report.items():
    print("\n")
    print(section.upper())
    print("----------------")

    print(result)
