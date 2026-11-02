#!/bin/bash
export ULTRON_TEST_MODE=1
export ULTRON_DRY_RUN=1


if pgrep -f "ultron_diagnostic_running" >/dev/null
then
    echo "Diagnostic already running"
    exit 1
fi

touch /tmp/ultron_diagnostic_running
trap "rm /tmp/ultron_diagnostic_running" EXIT
REPORT_DIR="reports"
mkdir -p "$REPORT_DIR"

DATE=$(date +"%Y-%m-%d_%H-%M-%S")
REPORT="$REPORT_DIR/ultron_diagnostic_$DATE.log"

PASS=0
FAIL=0
WARN=0


run_check()
{
    NAME="$1"
    COMMAND="$2"

    echo ""
    echo "================================="
    echo "$NAME"
    echo "================================="

    echo "[$NAME]" >> "$REPORT"

    bash -c "$COMMAND" >> "$REPORT" 2>&1

    if [ $? -eq 0 ]; then
        echo "✓ PASS"
        echo "PASS" >> "$REPORT"
        PASS=$((PASS+1))

    else
        echo "✗ FAILED"
        echo "FAILED" >> "$REPORT"
        FAIL=$((FAIL+1))
    fi
}


echo "ULTRON DIAGNOSTIC SYSTEM"
echo "Report: $REPORT"

echo "ULTRON DIAGNOSTIC SYSTEM" > "$REPORT"
echo "Date: $DATE" >> "$REPORT"


# 1 Syntax

run_check \
"Python Syntax" \
"python -m compileall ultron"



# 2 Tests

run_check \
"Unit Tests" \
"pytest --disable-warnings"


# 3 Import validation

run_check \
"Import Scanner" \
"python -m ultron.diagnostics"



# 4 Ruff

if command -v ruff >/dev/null
then

run_check \
"Code Quality Ruff" \
"ruff check ultron"

else
echo "WARNING: ruff missing"
WARN=$((WARN+1))
fi



# 5 Type checking

if command -v mypy >/dev/null
then

run_check \
"Type Checking" \
"mypy ultron"

else
echo "WARNING: mypy missing"
WARN=$((WARN+1))
fi



# 6 Dead code

if command -v vulture >/dev/null
then

run_check \
"Dead Code Scan" \
"vulture ultron"

else
echo "WARNING: vulture missing"
WARN=$((WARN+1))
fi



# 7 Dependencies

if command -v deptry >/dev/null
then

run_check \
"Dependency Check" \
"deptry ."

else
echo "WARNING: deptry missing"
WARN=$((WARN+1))
fi



# 8 Empty python files

run_check \
"Empty Python Files" \
"test -z \"$(find ultron -name '*.py' -empty)\""



# 9 Old files

echo ""
echo "OLD FILE CHECK"

OLD=$(find ultron -name "*old*" -o -name "*v1*")

if [ -z "$OLD" ]
then
echo "✓ No old files"
else
echo "⚠ Old files found:"
echo "$OLD"
echo "$OLD" >> "$REPORT"
WARN=$((WARN+1))
fi



# 10 Pass placeholders

echo ""
echo "PLACEHOLDER CHECK"

PLACE=$(grep -R "pass$" -n ultron --exclude-dir=__pycache__)

if [ -z "$PLACE" ]
then
echo "✓ No placeholders"
else
echo "⚠ Placeholder code:"
echo "$PLACE"
echo "$PLACE" >> "$REPORT"
WARN=$((WARN+1))
fi



echo ""
echo "================================="
echo "ULTRON FINAL REPORT"
echo "================================="

echo ""
echo "Passed : $PASS"
echo "Failed : $FAIL"
echo "Warnings : $WARN"

echo ""
echo "Full report:"
echo "$REPORT"


echo ""
echo "Status:"

if [ $FAIL -eq 0 ]
then
echo "✓ SYSTEM HEALTHY"
else
echo "✗ SYSTEM NEEDS ATTENTION"
fi
