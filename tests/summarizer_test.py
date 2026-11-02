from ultron.reasoning.chain_summarizer import ChainSummarizer


path=[

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


print(
ChainSummarizer().summarize(path)
)
