from ultron.knowledge.conflict.detector import ContradictionDetector


a = {

"concept":"Gravity",

"statement":
"Heavier objects fall faster"

}


b = {

"concept":"Gravity",

"statement":
"Objects fall at the same acceleration regardless of mass"

}


detector = ContradictionDetector()


result = detector.detect(
    a,
    b
)


print(result)
