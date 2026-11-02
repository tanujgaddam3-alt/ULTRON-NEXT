from ultron.learning.knowledge.builder import KnowledgeBuilder


data = {

"definitions":[

{
"title":"velocity",
"content":
"Velocity is rate of change of position"
}

],

"relationships":[
"Velocity depends on displacement and time"
],

"examples":[
"Car moving at constant speed"
],


"formulas":[
"v=v₀+at"
],


"misconceptions":[],

"records":[]

}



knowledge = KnowledgeBuilder().build(
"Kinematics",
data
)


print(knowledge)
