from ultron.reasoning.question.analyzer import QuestionAnalyzer


analyzer = QuestionAnalyzer()


questions=[

"What is velocity?",

"Why does force affect velocity?",

"How does acceleration change motion?"

]


for q in questions:

    print(
        analyzer.analyze(q)
    )
