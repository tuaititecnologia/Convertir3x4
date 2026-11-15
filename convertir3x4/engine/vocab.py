"""Vocabulario de escenas de la iglesia para la clasificación con CLIP.

Cada tag (en español, como lo ve el usuario) lleva frases en inglés, porque
CLIP entiende mejor el inglés. El vector del tag es el promedio de sus frases.

Si se cambia este vocabulario, subir VOCAB_VERSION: las imágenes ya analizadas
se vuelven a clasificar en la próxima pasada.
"""

VOCAB_VERSION = 1

VOCAB = {
    # --- fotos de actividades
    "alabanza": ["a worship band playing music on a church stage",
                 "people worshipping with raised hands at a concert",
                 "a singer with a microphone leading worship"],
    "predicación": ["a pastor preaching from a pulpit",
                    "a man speaking to an audience on a stage with a microphone",
                    "a preacher holding a bible on stage"],
    "bautismo": ["a baptism in a pool of water",
                 "a person being baptized in water by a pastor"],
    "oración": ["people praying with closed eyes in a church",
                "a group of people laying hands and praying"],
    "congregación": ["a crowd of people seated in a church auditorium",
                     "the audience of a church service seen from the stage"],
    "acción social": ["volunteers handing out donated clothes to people",
                      "volunteers delivering food donations in a poor neighborhood",
                      "a church outreach visiting houses in a humble neighborhood",
                      "people sorting piles of donated clothing"],
    "niños": ["children playing at a church kids ministry",
              "a group of kids with a teacher in a classroom"],
    "santa cena": ["communion bread and wine on a table",
                   "a cup of grape juice and bread for communion"],
    "boda": ["a wedding ceremony with a bride and groom"],
    "foto grupal": ["a group of people posing for a photo",
                    "a team photo of smiling people"],
    "retrato": ["a portrait photo of one person",
                "a selfie of a person"],
    "comida": ["people sharing a meal at tables",
               "a table with food"],
    "edificio": ["the exterior of a church building",
                 "a building facade on a street"],
    "paisaje": ["a landscape of nature", "a sunset sky"],
    # --- gráficos
    "placa": ["a sermon title graphic with large bold words",
              "a slide with a short title over a photo background",
              "a bible verse written on a slide",
              "a quote written in large letters"],
    "flyer": ["an event flyer with a date, time and place",
              "a promotional poster announcing an upcoming event with a date"],
    "infografía": ["an educational infographic with small text and icons",
                   "a presentation slide with bullet points and small pictures",
                   "a diagram explaining a process"],
    "fondo": ["an abstract colorful background texture",
              "a blurred bokeh background with lights",
              "a worship background graphic without text"],
    "logo": ["a logo on a plain background", "an emblem or brand mark"],
    "captura": ["a screenshot of a phone screen",
                "a screenshot of a chat conversation"],
    "documento": ["a photo of a paper document", "a scanned page with text"],
}
