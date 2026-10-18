import random


class RandomGenerator:
    @staticmethod
    def generate_cpf(prefix: str = ""):
        """Um CPF válido na conta dos dígitos, aleatório depois do prefixo.

        Sem prefixo, o primeiro dígito nunca é 0. Os casos especiais do mock
        da Receita (falecido, cancelado, lento...) começam todos com 00, e
        um CPF aleatório que caísse num deles faria um teste qualquer falhar
        uma vez a cada tantas rodadas, sem motivo aparente. Quem quer um
        caso especial pede pelo prefixo.
        """
        if prefix:
            cpf = [int(digit) for digit in prefix]
        else:
            cpf = [random.randrange(1, 10)]

        while len(cpf) < 9:
            cpf.append(random.randrange(10))

        for _ in range(2):
            value = sum([(len(cpf) + 1 - i) * v for i, v in enumerate(cpf)]) % 11
            cpf.append(11 - value if value > 1 else 0)
        cpf_number = "".join(str(x) for x in cpf)
        cpf_number = f"{cpf_number[0:3]}.{cpf_number[3:6]}.{cpf_number[6:9]}-{cpf_number[9:11]}"
        return cpf_number

    @staticmethod
    def generate_cnpj():
        cnpj = [random.randrange(1, 10)] + [random.randrange(10) for _ in range(7)] + [0, 0, 0, 1]

        for _ in range(2):
            value = sum(v * (i % 8 + 2) for i, v in enumerate(reversed(cnpj)))
            digit = 11 - value % 11
            cnpj.append(digit if digit < 10 else 0)

        cnpj_number = "".join(str(x) for x in cnpj)

        cnpj_number = (
            f"{cnpj_number[0:2]}.{cnpj_number[2:5]}.{cnpj_number[5:8]}/{cnpj_number[8:12]}-{cnpj_number[12:14]}"
        )

        return cnpj_number
