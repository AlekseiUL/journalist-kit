# Происхождение и методические ориентиры

Редакторские инструкции развивают предыдущую авторскую методику владельца
проекта. Публичная упаковка и небольшой диагностический скрипт созданы отдельно:
приватные данные, рабочие плагины, внутренний корпус голоса и runtime сторонних
Humanizer/RuText-пакетов не распространяются в этом репозитории.

Это не форк стороннего journalist/humanizer-пакета. Названия методов вроде
проверки первоисточника, лида, интервью или журналистики решений обозначают
редакторские практики, а не включённую программную зависимость.

## Методические ориентиры

- [Reuters: Standards and Values](https://reutersagency.com/about/standards-values/) — источники, атрибуция, достоверность и исправления.
- [SPJ: Code of Ethics](https://www.spj.org/spj-code-of-ethics/) — контекст, ответственность, возможный вред и независимость.
- [GIJN: Fact-checking](https://gijn.org/resource/introduction-investigative-journalism-fact-checking/) — проверка утверждений по исходным записям.
- [Poynter: The Nut Graf](https://www.poynter.org/archive/2003/the-nut-graf-part-i/) — раннее объяснение вопроса и значения истории.
- [Solutions Journalism Network: Four Pillars](https://www.solutionsjournalism.org/learning-lab/toolkits-guides/curriculum-builder/essentials-teaching-solutions-journalism/4-pillars) — ответ на проблему, доказательства, переносимый опыт и ограничения.

Ссылки даны для атрибуции и дальнейшего чтения. Учебные статьи, их иллюстрации
и большие выдержки не включены; их содержание не становится лицензированным
под лицензией этого проекта. Профессиональные кодексы не являются универсальной
юридической инструкцией и не означают одобрения проекта этими организациями.

## Формат и интеграция

- [Agent Skills](https://agentskills.io/specification) — формат каталога навыка.
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills) — документированный механизм загрузки навыков.

Hermes, Agent Skills и названия сторонних систем принадлежат соответствующим
владельцам. Проект независим и не утверждает официальное партнёрство.

## Исполнение

Runtime-скрипты используют только стандартную библиотеку Python; Python не
поставляется с пакетом. GitHub Actions использует официальные checkout/setup-python
actions только в CI. Они не входят в устанавливаемый навык.
