from ultron.reasoning.explanation_builder import ExplanationBuilder


path = [

{
"from":"Force",
"relation":"causes",
"to":"Acceleration"
},

{
"from":"Acceleration",
"relation":"changes",
"to":"Velocity"
}

]


answer = ExplanationBuilder().build(
    path
)


print(answer)
