# MediPet 场景 Skills

应用从 `MEDIPET_SKILLS_DIR` 加载四组规则：医院公开信息、预约与取消协助、材料流程与院内指引、就诊助手服务边界。Skill 注入角色提示词，工具白名单和预约事务仍由程序执行。

每组使用独立目录下的 `SKILL.md`。顶部支持 name、description、keywords、agents、enabled。关键词及角色列表用英文逗号分隔；keywords 为空表示该角色常驻规则。角色值为 general、guidance、appointment、escalation。

公开信息规则按医院领域关键词匹配 General；预约和指引规则常驻对应角色；服务边界常驻全部四个角色，承接消息没有关键词时也生效。内容按既有提示词长度预算截断，因此先写最关键的规则。

文件修改不会自动生效。通过 `GET /skills` 查看已加载规则与解析错误，调用 `POST /skills/reload` 后，新请求使用更新内容。页面也提供查看和主动重载入口。
