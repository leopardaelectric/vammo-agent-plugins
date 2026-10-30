# Português — adaptação de princípios de clareza

Esta referência adapta princípios de clareza do ASD-STE100 para português. Não é STE formal
nem uma tradução do dicionário normativo inglês. Português é permitido imediatamente.

## Redação

- Comece pelo objeto, sintoma, pergunta ou resultado que orienta a próxima ação.
- Use palavras comuns e os termos da operação. Mantenha o mesmo termo para o mesmo conceito.
- Escreva uma ideia por frase. Use voz ativa quando o ator for conhecido; não invente quem age.
- Em passos, use uma ação por passo. Coloque a condição antes da ação.
- Prefira verbos concretos: “verificar”, em vez de “realizar a verificação”.
- Use Markdown para separar informações que o leitor precisa comparar ou executar.
  Não crie seções vazias nem repita projeto, responsável ou prioridade já presentes nos campos.
- Preserve literais: `bin_code`, `LOTE-A`, comandos, rotas e mensagens de erro.
- Mantenha a força do texto: “precisa” expressa obrigação; “não pode”, proibição;
  “pode”, permissão; “recomendo”, sugestão. Não troque uma pela outra para encurtar.
- Identifique desconhecidos. Uma hipótese continua hipótese; um resultado parcial continua parcial.

## Exemplo sintético

Fonte: “Na conferência da remessa REM-04, há 3 peças sem código. A operação precisa registrar
o código dessas peças antes de fechar a conferência. Não sabemos por que estão sem código.”

Título: Registrar o código de 3 peças da remessa REM-04

```markdown
## Situação
Há 3 peças sem código na remessa REM-04. Não sabemos por que estão sem código.

## Resultado esperado
A operação precisa registrar o código das 3 peças antes de fechar a conferência.
```

O exemplo conserva quantidade, remessa, ator, obrigação, condição e incerteza. Não define causa,
prazo, responsável nominal ou solução. Se a tarefa ainda for uma solicitação para triagem,
descreva a necessidade e o que falta saber, sem convertê-la em trabalho de implementação.

Ao revisar um card português, preserve o idioma. Ao adicionar progresso, entregue a nota adicional
e mantenha o texto humano e seus critérios. A adaptação não exige um aviso dentro de cada card.
